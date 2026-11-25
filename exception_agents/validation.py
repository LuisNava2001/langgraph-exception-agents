"""Deterministic validation gate: the final authority before any recommendation is returned.

It runs after the critic (and after a human decision), whatever they concluded. Each check is
a hard constraint over the computed facts; a model, the critic or a reviewer cannot waive one.
The only thing a human approval satisfies is the human-approval requirement itself.
"""

from __future__ import annotations

from .models import AnalysisResult, Draft, Finding, ResearchResult, ValidationReport

TECH_ACTIONS = ("DISPATCH_TECHNICIAN", "REASSIGN_TECHNICIAN", "WAIT_FOR_PART_WITH_MITIGATION")


def validate(facts: AnalysisResult, research: ResearchResult, draft: Draft, human_review: dict | None,
             kb_doc_ids: set[str]) -> ValidationReport:
    evidence_docs = {h.doc_id for h in research.evidence}
    diagnostic_docs = {h.doc_id for h in research.evidence if h.category == "diagnostic"}
    valid_support = {h.chunk_id for h in research.evidence} | set(facts.fact_ids)
    needs_tech = draft.action in TECH_ACTIONS
    approved = bool(human_review and human_review.get("decision") == "approve")

    checks = [
        ("V01_NO_FORBIDDEN_ACTION", "block", facts.forbidden_requested_action is None,
         f"requested action {facts.forbidden_requested_action} is forbidden" if facts.forbidden_requested_action else "none requested"),
        ("V02_EVIDENCE_AVAILABLE", "escalate", not research.insufficient and bool(diagnostic_docs),
         f"{len(diagnostic_docs)} diagnostic article(s) retrieved"),
        ("V03_TECHNICIAN_ELIGIBLE", "revise", (not needs_tech) or draft.technician_id in facts.eligible_technicians,
         f"assigned {draft.technician_id}; eligible {facts.eligible_technicians}"),
        ("V04_PARTS_PROMISE", "revise",
         (facts.parts_available or draft.action not in ("DISPATCH_TECHNICIAN", "REASSIGN_TECHNICIAN"))
         and not (draft.expected_completion and facts.earliest_completion and draft.expected_completion < facts.earliest_completion),
         f"parts available: {facts.parts_available}; promised {draft.expected_completion}, earliest {facts.earliest_completion}"),
        ("V05_SLA_TRUTHFUL", "revise", draft.sla_claim == facts.sla_status,
         f"claimed {draft.sla_claim}, computed {facts.sla_status}"),
        ("V06_POLICIES_APPLIED", "revise",
         set(facts.required_policies) <= set(draft.policies_applied) and (not facts.mitigation_required or bool(draft.mitigation)),
         f"required {facts.required_policies}"),
        ("V07_CITATIONS_VALID", "revise",
         bool(draft.citations) and all(c in kb_doc_ids and c in evidence_docs for c in draft.citations)
         and bool(set(draft.citations) & diagnostic_docs),
         f"cited {draft.citations}; citable {sorted(evidence_docs)}"),
        ("V08_CLAIMS_SUPPORTED", "revise",
         all(claim.support and all(s in valid_support for s in claim.support) for claim in draft.claims),
         f"{len(draft.claims)} claim(s)"),
        ("V09_REQUIRED_FIELDS", "revise",
         bool(draft.rationale.strip()) and (not needs_tech or (draft.technician_id and draft.expected_completion)),
         "technician, completion and rationale present"),
        ("V10_HUMAN_APPROVAL", "human_review", not facts.human_triggers or approved,
         f"triggers {facts.human_triggers}; human approval {'recorded' if approved else 'missing'}"),
    ]
    results = [{"check_id": cid, "passed": bool(ok), "detail": detail} for cid, _, ok, detail in checks]
    failures = [Finding(code=cid, severity={"block": "block", "escalate": "evidence", "revise": "fix",
                                            "human_review": "human"}[route], message=detail)
                for cid, route, ok, detail in checks if not ok]
    failed_routes = {route for _, route, ok, _ in checks if not ok}
    for route in ("block", "escalate", "revise", "human_review"):
        if route in failed_routes:
            break
    else:
        route = "respond"
    return ValidationReport(passed=not failures, checks=results, failures=failures, route=route)
