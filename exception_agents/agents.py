"""Agent nodes. Each takes the shared state and returns only the fields it owns (see graph.py).

Nodes read typed slices of the state, validate them with Pydantic, and return plain dicts.
They have no side effects outside the returned update.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from langgraph.types import interrupt

from . import critic as critic_agent
from . import response
from .config import Settings, knowledge_base
from .models import (
    AnalysisResult,
    Critique,
    Draft,
    ExceptionCase,
    Finding,
    Hit,
    HumanDecision,
    HumanReviewRecord,
    IntakeResult,
    ResearchResult,
    ValidationReport,
)
from .reasoners import DraftContext, Reasoner, ScriptedReasoner
from .retrieval import BM25Index, build_index, injection_flags
from .rules import compute_facts
from .validation import validate as validate_draft

EXCEPTION_KEYWORDS = {
    "TECHNICIAN_UNAVAILABLE": "technician assignment reassign eligible unavailable",
    "PART_DELAY": "spare part arrival expedite delay",
    "SLA_BREACH": "sla breach deadline breached",
    "SLA_AT_RISK": "sla deadline at risk",
    "URGENT_SAFETY": "safety hazard lockout urgent",
    "CONTRADICTORY_REPORT": "contradictory field reports disagree",
    "RESOURCE_CONFLICT": "competing work orders priority technician",
    "COST_APPROVAL": "cost approval human review",
    "INCORRECT_RECOMMENDATION": "technician assignment eligible",
    "EQUIPMENT_FAILURE": "repair diagnosis",
}


@dataclass(frozen=True)
class Controls:
    """Negative-control switches for the evaluation harness. All off in normal runs."""

    name: str = "none"
    critic_off: bool = False
    validator_off: bool = False
    retrieval_off: bool = False
    injection_filter_off: bool = False


CONTROLS = {
    "none": Controls(),
    "critic_off": Controls("critic_off", critic_off=True),
    "validator_off": Controls("validator_off", validator_off=True),
    "critic_and_validator_off": Controls("critic_and_validator_off", critic_off=True, validator_off=True),
    "retrieval_off": Controls("retrieval_off", retrieval_off=True),
    "injection_filter_off": Controls("injection_filter_off", injection_filter_off=True),
}


@dataclass
class Deps:
    settings: Settings
    index: BM25Index
    kb: dict
    reasoner: Reasoner
    controls: Controls = field(default_factory=Controls)

    @property
    def kb_ids(self) -> set[str]:
        return set(self.kb)


def make_deps(controls: str = "none", reasoner: Reasoner | None = None) -> Deps:
    articles = knowledge_base()
    chosen = CONTROLS[controls]
    reasoner = reasoner or ScriptedReasoner(obey_injections=chosen.injection_filter_off)
    return Deps(Settings.load(), build_index(articles), {a["doc_id"]: a for a in articles}, reasoner, chosen)


def _case(state: dict) -> ExceptionCase:
    return ExceptionCase.model_validate(state["case"])


# --- Intake ----------------------------------------------------------------------------------

def intake(state: dict, deps: Deps) -> dict:
    case = _case(state)
    request, equipment = case.service_request, case.equipment
    missing = []
    if not request.priority:
        missing.append("service_request.priority")
    if equipment is None or not request.equipment_id:
        missing.append("equipment")
    if not (request.symptom or "").strip():
        missing.append("service_request.symptom")
    if not case.technicians:
        missing.append("technicians")
    texts = [case.exception.summary, request.symptom or ""] + [f.value for f in case.reported_facts]
    flags = sorted({flag for text in texts for flag in injection_flags(text)})
    terms = [request.symptom or "", EXCEPTION_KEYWORDS.get(case.exception.type, ""),
             "service level agreement resolution target"]
    if equipment:
        terms.insert(0, equipment.category.replace("_", " "))
        terms += [f"{hazard} safety" for hazard in equipment.hazards]
    result = IntakeResult(
        case_id=case.case_id, request_id=request.request_id, incident_type=case.exception.type, priority=request.priority,
        status="MISSING_DATA" if missing else "READY", missing_fields=missing,
        evidence_provided=[f"{f.source}: {f.field}={f.value}" for f in case.reported_facts],
        untrusted_input_flags=flags, query_terms=[t for t in terms if t.strip()],
    )
    summary = f"{result.status}; missing {missing}" if missing else f"READY; {len(case.technicians)} technicians, {len(case.parts)} parts"
    if flags:
        summary += f"; instruction-like text in request ignored ({', '.join(flags)})"
    return {"intake": result.model_dump(mode="json"), "_summary": summary}


# --- Research --------------------------------------------------------------------------------

def research(state: dict, deps: Deps) -> dict:
    case = _case(state)
    intake_result = IntakeResult.model_validate(state["intake"])
    query = " ".join(intake_result.query_terms)
    found = [] if deps.controls.retrieval_off else deps.index.search(query, deps.settings.top_k)
    category = case.equipment.category if case.equipment else None
    hits = []
    for chunk, score in found:
        flags = [] if deps.controls.injection_filter_off else deps.index.doc_flags.get(chunk.doc_id, [])
        hits.append(Hit(chunk_id=chunk.chunk_id, doc_id=chunk.doc_id, title=chunk.title, category=chunk.category,
                        snippet=chunk.text, score=round(score, 4), flagged=bool(flags), flag_reason=", ".join(flags) or None))

    def on_topic(hit: Hit) -> bool:  # a diagnostic article for another equipment class is not evidence
        return hit.category != "diagnostic" or category in deps.kb[hit.doc_id]["equipment"]

    evidence = [h for h in hits if not h.flagged and h.score >= deps.settings.min_score and on_topic(h)]
    diagnostic = [h for h in evidence if h.category == "diagnostic"]
    result = ResearchResult(
        query=query, hits=hits, evidence=evidence, flagged=[h for h in hits if h.flagged], insufficient=not diagnostic,
        reason=None if diagnostic else f"No diagnostic article for {category or 'this equipment'} scored above {deps.settings.min_score}.",
    )
    summary = (f"{len(hits)} hits, {len(evidence)} citable, {len(result.flagged)} flagged; "
               + ("insufficient evidence" if result.insufficient else f"top diagnostic {diagnostic[0].doc_id}"))
    return {"research": result.model_dump(mode="json"), "_summary": summary}


# --- Analysis --------------------------------------------------------------------------------

def analysis(state: dict, deps: Deps) -> dict:
    case = _case(state)
    research_result = ResearchResult.model_validate(state["research"])
    facts = compute_facts(case, deps.settings)
    drafts = state.get("drafts", [])
    feedback = []
    if drafts:  # revision: hand the latest critic and validator findings to the reasoner
        if state.get("critiques"):
            feedback += [Finding.model_validate(f) for f in state["critiques"][-1]["findings"]]
        if state.get("validations") and state["validations"][-1]["draft_index"] == len(drafts) - 1:
            feedback += [Finding.model_validate(f) for f in state["validations"][-1]["failures"]]
    draft = deps.reasoner.draft(DraftContext(case, facts, research_result, feedback, draft_index=len(drafts)))
    data = draft.model_dump(mode="json")
    summary = (f"draft {draft.draft_index} ({draft.source}): {draft.action} {draft.technician_id or ''}; "
               f"SLA {facts.sla_status}; eligible {facts.eligible_technicians}")
    return {"analysis": facts.model_dump(mode="json"), "draft": data, "drafts": [data], "_summary": summary}


# --- Critic ----------------------------------------------------------------------------------

def human_approval_for(state: dict, draft_index: int) -> dict | None:
    """A human approval counts only for the draft the reviewer saw."""
    record = state.get("human_review")
    if record and record.get("decision") == "approve" and record.get("draft_index") == draft_index:
        return record
    return None


def critic(state: dict, deps: Deps) -> dict:
    case = _case(state)
    facts = AnalysisResult.model_validate(state["analysis"])
    research_result = ResearchResult.model_validate(state["research"])
    draft = Draft.model_validate(state["draft"])
    if deps.controls.critic_off:
        critique = Critique(verdict="APPROVED", findings=[], draft_index=draft.draft_index)
    else:
        critique = critic_agent.review(case, facts, research_result, draft, deps.kb_ids,
                                       human_approved=human_approval_for(state, draft.draft_index) is not None)
    data = critique.model_dump(mode="json")
    codes = ", ".join(sorted({f.code for f in critique.findings})) or "no findings"
    return {"critique": data, "critiques": [data], "_summary": f"{critique.verdict}: {codes}"}


# --- Validation gate -------------------------------------------------------------------------

def validate(state: dict, deps: Deps) -> dict:
    facts = AnalysisResult.model_validate(state["analysis"])
    research_result = ResearchResult.model_validate(state["research"])
    draft = Draft.model_validate(state["draft"])
    if deps.controls.validator_off:
        report = ValidationReport(passed=True, checks=[], failures=[], route="respond")
    else:
        report = validate_draft(facts, research_result, draft, human_approval_for(state, draft.draft_index), deps.kb_ids)
    data = report.model_copy(update={"draft_index": draft.draft_index}).model_dump(mode="json")
    failed = ", ".join(f.code for f in report.failures) or "all checks passed"
    return {"validation": data, "validations": [data], "_summary": f"{report.route}: {failed}"}


# --- Human in the loop -----------------------------------------------------------------------

def human_review(state: dict, deps: Deps) -> dict:
    """Pause with interrupt(); resume with Command(resume={...}). No side effects before interrupt():
    LangGraph re-runs this node from the top when it resumes."""
    draft = state["draft"]
    validation = state.get("validation") or {}
    if validation.get("draft_index") == draft["draft_index"] and validation.get("route") == "human_review":
        source = validation.get("failures", [])  # sent here by the validation gate
    else:
        source = (state.get("critique") or {}).get("findings", [])  # sent here by the critic
    triggers = [f["code"] for f in source if f["severity"] == "human"]
    payload = {
        "case_id": state["case"]["case_id"], "request_id": state["case"]["service_request"]["request_id"],
        "question": "Approve this recommendation?", "allowed_decisions": ["approve", "reject"], "triggers": triggers,
        "draft": {k: draft[k] for k in ("action", "technician_id", "sla_claim", "expected_completion", "estimated_cost",
                                        "citations", "draft_index")},
    }
    decision = HumanDecision.model_validate(interrupt(payload))
    record = HumanReviewRecord(decision=decision.decision, reviewer_id=decision.reviewer_id, note=decision.note,
                               simulated=decision.simulated, triggers=triggers, draft_index=draft["draft_index"])
    data = record.model_dump(mode="json")
    who = f"{decision.reviewer_id} (simulated)" if decision.simulated else decision.reviewer_id
    return {"human_review": data, "_summary": f"{decision.decision} by {who}; triggers {triggers}"}


# --- Terminal nodes --------------------------------------------------------------------------

def escalation_reason(state: dict) -> tuple[str, str]:
    if state.get("errors"):
        error = state["errors"][-1]
        return "AGENT_ERROR", f"{error['node']} failed: {error['type']}: {error['message']}"
    intake_result = state.get("intake") or {}
    if intake_result.get("status") == "MISSING_DATA":
        return "MISSING_DATA", "Missing: " + ", ".join(intake_result["missing_fields"])
    human = state.get("human_review") or {}
    if human.get("decision") == "reject":
        return "HUMAN_REJECTED", f"Rejected by {human['reviewer_id']}: {human.get('note', '')}".strip()
    critique = state.get("critique") or {}
    validation = state.get("validation") or {}
    if critique.get("verdict") == "INSUFFICIENT_EVIDENCE" or validation.get("route") == "escalate":
        return "INSUFFICIENT_EVIDENCE", (state.get("research") or {}).get("reason") or "No diagnostic evidence."
    return "REVISION_LIMIT", f"Still failing after {max(len(state.get('drafts', [])) - 1, 0)} revision(s)."


def respond(state: dict, deps: Deps) -> dict:
    final = response.resolved(state)
    return {"final": final, "outcome": final["outcome"], "_summary": final["outcome"]}


def escalate(state: dict, deps: Deps) -> dict:
    reason, details = escalation_reason(state)
    final = response.escalated(state, reason, details)
    return {"final": final, "outcome": final["outcome"], "_summary": f"{final['outcome']}: {reason}"}


def block(state: dict, deps: Deps) -> dict:
    findings = (state.get("critique") or {}).get("findings", []) + (state.get("validation") or {}).get("failures", [])
    details = next((f["message"] for f in findings if f["severity"] == "block"), "A forbidden action was requested.")
    final = response.blocked(state, details)
    return {"final": final, "outcome": final["outcome"], "_summary": f"BLOCKED: {details}"}
