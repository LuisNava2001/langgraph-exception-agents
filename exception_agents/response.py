"""Response agent: turns the validated state into the final explanation.

It only reports what is in the state: the evidence the research agent retrieved (never the
flagged chunks), the draft that passed validation, and the critic's and validator's findings.
Confidence is a deterministic rating with its reasons listed, not a model probability.
"""

from __future__ import annotations

from .models import Claim, FinalResponse

ESCALATION_TEXT = {
    "MISSING_DATA": "Required request data is missing; the case cannot be analysed.",
    "INSUFFICIENT_EVIDENCE": "No diagnostic evidence supports a recommendation; the system declines to guess.",
    "REVISION_LIMIT": "The draft still failed review after the maximum number of revisions.",
    "AGENT_ERROR": "An agent failed; nothing was decided.",
    "HUMAN_REJECTED": "The human reviewer rejected the recommendation.",
}


def _evidence(research: dict | None) -> list[dict]:
    if not research:
        return []
    return [{"chunk_id": h["chunk_id"], "doc_id": h["doc_id"], "title": h["title"], "score": h["score"],
             "snippet": h["snippet"]} for h in research["evidence"]]


def _limitations(state: dict) -> list[str]:
    items = []
    draft = state.get("draft") or {}
    if draft.get("source") == "scripted":
        items.append("Demo Mode: the draft came from a scripted reasoner, not a language model.")
    items.append("Retrieval is lexical (BM25); an article worded differently from the request can be missed.")
    research = state.get("research") or {}
    flagged = sorted({h["doc_id"] for h in research.get("flagged", [])})
    if flagged:
        items.append(f"Retrieved content with instruction-like text was excluded from evidence: {', '.join(flagged)}.")
    intake = state.get("intake") or {}
    if intake.get("untrusted_input_flags"):
        items.append("Instruction-like text in the request itself was treated as data and ignored.")
    if state.get("human_review"):
        items.append("Reviewer names are local labels, not authenticated identities.")
    return items


def _confidence(state: dict) -> dict:
    research, analysis = state.get("research") or {}, state.get("analysis") or {}
    diagnostic_docs = {h["doc_id"] for h in research.get("evidence", []) if h["category"] == "diagnostic"}
    revisions = max(len(state.get("drafts", [])) - 1, 0)
    score, factors = 3, []
    if len(diagnostic_docs) >= 2:
        factors.append(f"{len(diagnostic_docs)} diagnostic articles support the diagnosis")
    else:
        score -= 1
        factors.append("a single diagnostic article supports the diagnosis")
    if revisions:
        score -= 1
        factors.append(f"the first draft needed {revisions} revision(s)")
    if analysis.get("contradictions"):
        score -= 1
        factors.append("field reports contradicted each other (resolved by a reviewer)")
    if analysis.get("sla_status") in ("AT_RISK", "BREACHED"):
        factors.append(f"SLA is {analysis.get('sla_status')}; this is a fact, not uncertainty")
    level = "HIGH" if score >= 3 else "MEDIUM" if score == 2 else "LOW"
    return {"level": level, "factors": factors}


def _render(final: dict) -> str:
    lines = [f"Case {final['case_id']}: {final['outcome']}.", final["summary"]]
    rec = final.get("recommendation")
    if rec:
        lines.append(f"Recommendation: {rec['action']}" + (f" with technician {rec['technician_id']}" if rec.get("technician_id") else "")
                     + f"; SLA {rec['sla_claim']}; expected completion {rec.get('expected_completion')}.")
        if rec.get("mitigation"):
            lines.append(f"Mitigation: {rec['mitigation']}")
    if final["citations"]:
        lines.append("Evidence: " + "; ".join(f"[{c}]" for c in final["citations"]))
    if final["escalation"]:
        lines.append(f"Escalation: {final['escalation']['reason']}: {final['escalation']['details']}")
    lines.append(f"Confidence: {final['confidence']['level']} ({'; '.join(final['confidence']['factors'])}).")
    return "\n".join(lines)


def _base(state: dict, outcome: str, summary: str) -> dict:
    case = state["case"]
    return {
        "case_id": case["case_id"], "outcome": outcome, "summary": summary, "recommendation": None,
        "evidence": _evidence(state.get("research")), "alternatives": [], "claims": [], "citations": [],
        "confidence": {"level": "LOW", "factors": ["no recommendation was approved"]},
        "limitations": _limitations(state), "escalation": None, "human_review": state.get("human_review"),
        "revisions": max(len(state.get("drafts", [])) - 1, 0), "explanation": "",
    }


def incident_summary(state: dict) -> str:
    case = state["case"]
    request, exception = case["service_request"], case["exception"]
    equipment = (case.get("equipment") or {}).get("category", "unknown equipment")
    return (f"{exception['type'].replace('_', ' ').title()} on {request['request_id']} "
            f"({request.get('priority') or 'no priority'}, {equipment.replace('_', ' ')} at {case['location']['name']}): "
            f"{exception['summary']}")


def resolved(state: dict) -> dict:
    draft, analysis = state["draft"], state["analysis"]
    human = state.get("human_review")
    outcome = "RESOLVED_AFTER_REVIEW" if human and human.get("decision") == "approve" else "RESOLVED"
    final = _base(state, outcome, incident_summary(state))
    options = {o["option_id"]: o for o in analysis["options"]}
    final.update(
        recommendation={k: draft[k] for k in ("action", "technician_id", "part_ids", "sla_claim", "expected_completion",
                                              "mitigation", "policies_applied", "estimated_cost")},
        alternatives=[{"option_id": o, "description": options[o]["description"]} for o in draft["alternatives"] if o in options],
        claims=[Claim.model_validate(c).model_dump() for c in draft["claims"]],
        citations=list(draft["citations"]), confidence=_confidence(state),
    )
    final["explanation"] = _render(final)
    return FinalResponse.model_validate(final).model_dump(mode="json")


def escalated(state: dict, reason: str, details: str) -> dict:
    outcome = "REJECTED" if reason == "HUMAN_REJECTED" else "ESCALATED"
    final = _base(state, outcome, incident_summary(state))
    critiques = state.get("critiques", [])
    open_findings = [f["code"] for f in (critiques[-1]["findings"] if critiques else [])]
    final["escalation"] = {"reason": reason, "details": details or ESCALATION_TEXT[reason], "open_findings": open_findings}
    final["explanation"] = _render(final)
    return FinalResponse.model_validate(final).model_dump(mode="json")


def blocked(state: dict, details: str) -> dict:
    final = _base(state, "BLOCKED", incident_summary(state))
    final["escalation"] = {"reason": "POLICY_VIOLATION", "details": details, "open_findings": ["FORBIDDEN_ACTION"]}
    final["explanation"] = _render(final)
    return FinalResponse.model_validate(final).model_dump(mode="json")
