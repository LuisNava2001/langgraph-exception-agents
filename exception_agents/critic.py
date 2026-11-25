"""Critic agent: reviews a draft against the facts and the evidence and picks the next route.

Findings carry a severity that decides the verdict:
``block`` -> POLICY_VIOLATION, ``evidence`` -> INSUFFICIENT_EVIDENCE, ``fix`` -> REVISE,
``human`` -> HUMAN_REVIEW; no findings -> APPROVED. The critic can suggest corrections; it cannot
approve anything on its own, because the deterministic validator runs after it.
"""

from __future__ import annotations

from .config import policies
from .models import AnalysisResult, Critique, Draft, ExceptionCase, Finding, ResearchResult

TECH_ACTIONS = ("DISPATCH_TECHNICIAN", "REASSIGN_TECHNICIAN", "WAIT_FOR_PART_WITH_MITIGATION")
HUMAN_TRIGGER_TEXT = {
    "COST_APPROVAL_REQUIRED": "Estimated cost is above the approval threshold (POL-COST-APPROVAL).",
    "CONTRADICTION": "Field reports contradict each other (POL-CONTRADICTION); a person must decide.",
    "PRIORITY_TIE": "Another order with the same priority and deadline holds this technician (POL-PRIORITY-CONFLICT).",
}


def _f(code: str, severity: str, message: str, suggestion: str | None = None) -> Finding:
    return Finding(code=code, severity=severity, message=message, suggestion=suggestion)


def review(case: ExceptionCase, facts: AnalysisResult, research: ResearchResult, draft: Draft,
           kb_doc_ids: set[str], human_approved: bool) -> Critique:
    findings: list[Finding] = []
    if facts.forbidden_requested_action:
        findings.append(_f("FORBIDDEN_ACTION", "block",
                           f"The request asks for {facts.forbidden_requested_action}, which POL-FORBIDDEN-ACTIONS forbids."))
    if research.insufficient:
        findings.append(_f("INSUFFICIENT_EVIDENCE", "evidence", research.reason or "No diagnostic evidence was retrieved."))

    suggestion = f"Use eligible technician {facts.policy_technician}." if facts.policy_technician else None
    techs = {t.technician_id: t for t in facts.technicians}
    if draft.action in TECH_ACTIONS:
        tech = techs.get(draft.technician_id) if draft.technician_id else None
        if draft.technician_id is None:
            findings.append(_f("MISSING_FIELD", "fix", "The draft assigns no technician.", suggestion))
        elif tech is None:
            findings.append(_f("UNKNOWN_TECHNICIAN", "fix", f"{draft.technician_id} is not in the roster.", suggestion))
        else:
            checks = [
                (not tech.qualified, "SKILL_MISMATCH", f"{tech.technician_id} lacks skill {case.service_request.required_skill}."),
                (tech.qualified and not tech.certified, "CERTIFICATION_MISSING", f"{tech.technician_id} lacks a required certification."),
                (not tech.region_match, "REGION_MISMATCH", f"{tech.technician_id} works in another region."),
                (not tech.available, "TECHNICIAN_UNAVAILABLE", f"{tech.technician_id} is on leave or off shift for the repair window."),
                (tech.booked_by_higher_or_equal, "TECHNICIAN_BOOKED", f"{tech.technician_id} is held by a competing order with precedence."),
            ]
            findings += [_f(code, "fix", message, suggestion) for failed, code, message in checks if failed]
    elif draft.action == "ESCALATE_TO_SUPERVISOR":
        findings.append(_f("NO_FEASIBLE_ASSIGNMENT", "human", "No eligible technician; a dispatcher must decide."))

    if not facts.parts_available and draft.action in ("DISPATCH_TECHNICIAN", "REASSIGN_TECHNICIAN"):
        missing = ", ".join(p.part_id for p in facts.parts if not p.in_stock)
        findings.append(_f("PART_NOT_AVAILABLE", "fix", f"The draft dispatches a repair whose parts are not in stock ({missing}).",
                           "Wait for the part with the required mitigation (KA-024)."))
    if draft.expected_completion and facts.earliest_completion and draft.expected_completion < facts.earliest_completion:
        findings.append(_f("UNREALISTIC_COMPLETION", "fix",
                           f"Promised completion {draft.expected_completion} is earlier than possible ({facts.earliest_completion})."))
    if draft.sla_claim != facts.sla_status:
        findings.append(_f("SLA_MISREPRESENTED", "fix", f"The draft says SLA {draft.sla_claim}; the facts say {facts.sla_status}.",
                           f"State SLA status {facts.sla_status}."))
    for policy in sorted(set(facts.required_policies) - set(draft.policies_applied)):
        findings.append(_f("POLICY_OMITTED", "fix", f"Required policy {policy} is not applied.", f"Apply {policy}."))
    if facts.mitigation_required and not draft.mitigation:
        findings.append(_f("POLICY_OMITTED", "fix", "The required mitigation is missing.", facts.mitigation_required))

    evidence_docs = {h.doc_id for h in research.evidence}
    flagged_docs = {h.doc_id for h in research.flagged}
    for doc in draft.citations:
        if doc not in kb_doc_ids:
            findings.append(_f("CITATION_NOT_FOUND", "fix", f"{doc} does not exist in the knowledge base."))
        elif doc in flagged_docs:
            findings.append(_f("UNTRUSTED_SOURCE_CITED", "fix", f"{doc} contains instruction-like text and was excluded from evidence."))
        elif doc not in evidence_docs:
            findings.append(_f("CITATION_NOT_RETRIEVED", "fix", f"{doc} was not retrieved for this case."))
    diagnostic = {h.doc_id for h in research.evidence if h.category == "diagnostic"}
    if not research.insufficient and not set(draft.citations) & diagnostic:
        findings.append(_f("MISSING_EVIDENCE_CITATION", "fix", "No retrieved diagnostic article is cited (POL-EVIDENCE)."))

    valid_support = {h.chunk_id for h in research.evidence} | set(facts.fact_ids)
    for claim in draft.claims:
        unknown = [s for s in claim.support if s not in valid_support]
        if not claim.support or unknown:
            findings.append(_f("UNSUPPORTED_CLAIM", "fix", f"Claim '{claim.text}' has no valid support ({', '.join(unknown) or 'none'})."))
    if not draft.rationale.strip():
        findings.append(_f("MISSING_FIELD", "fix", "The draft has no rationale."))

    if not human_approved:
        findings += [_f(trigger, "human", HUMAN_TRIGGER_TEXT[trigger]) for trigger in facts.human_triggers]

    severities = {f.severity for f in findings}
    if "block" in severities:
        verdict = "POLICY_VIOLATION"
    elif "evidence" in severities:
        verdict = "INSUFFICIENT_EVIDENCE"
    elif "fix" in severities:
        verdict = "REVISE"
    elif "human" in severities:
        verdict = "HUMAN_REVIEW"
    else:
        verdict = "APPROVED"
    return Critique(verdict=verdict, findings=findings, draft_index=draft.draft_index)


def policy_doc(policy_id: str) -> str:
    return policies()["policies"][policy_id]["doc_id"]
