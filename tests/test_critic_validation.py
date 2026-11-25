"""Critic findings for every injected fault, and the deterministic gate as the final authority."""

import pytest

from exception_agents import critic, validation
from exception_agents.agents import make_deps
from exception_agents.models import AnalysisResult, ExceptionCase, ResearchResult
from exception_agents.reasoners import DraftContext, apply_fault, clean_draft
from exception_agents.rules import compute_facts
from exception_agents.runner import run_simulated

from .conftest import first_case


def context_for(case, values):
    return DraftContext(ExceptionCase.model_validate(case.model_dump()), AnalysisResult.model_validate(values["analysis"]),
                        ResearchResult.model_validate(values["research"]))


def review(case, values, draft, approved=False, deps=None):
    deps = deps or make_deps()
    return critic.review(case, AnalysisResult.model_validate(values["analysis"]), ResearchResult.model_validate(values["research"]),
                         draft, deps.kb_ids, human_approved=approved)


def gate(values, draft, human=None, deps=None):
    deps = deps or make_deps()
    return validation.validate(AnalysisResult.model_validate(values["analysis"]), ResearchResult.model_validate(values["research"]),
                               draft, human, deps.kb_ids)


def test_a_clean_draft_passes_critic_and_gate(cases, runs):
    case = first_case(cases, "correct_resolution")
    draft = clean_draft(context_for(case, runs[case.case_id]))
    assert review(case, runs[case.case_id], draft).verdict == "APPROVED"
    report = gate(runs[case.case_id], draft)
    assert report.passed and report.route == "respond" and len(report.checks) == 10


FAULT_CASES = [
    ("ASSIGN_UNAVAILABLE_TECH", "critic_revision", "TECHNICIAN_UNAVAILABLE", "V03_TECHNICIAN_ELIGIBLE"),
    ("UNKNOWN_TECHNICIAN", "critic_revision", "UNKNOWN_TECHNICIAN", "V03_TECHNICIAN_ELIGIBLE"),
    ("SKILL_MISMATCH", "correct_resolution", "SKILL_MISMATCH", "V03_TECHNICIAN_ELIGIBLE"),
    ("SLA_MISREPRESENTED", "sla_breached", "SLA_MISREPRESENTED", "V05_SLA_TRUTHFUL"),
    ("OMIT_POLICY", "sla_breached", "POLICY_OMITTED", "V06_POLICIES_APPLIED"),
    ("CITE_MISSING_DOC", "correct_resolution", "CITATION_NOT_FOUND", "V07_CITATIONS_VALID"),
    ("UNSUPPORTED_CLAIM", "correct_resolution", "UNSUPPORTED_CLAIM", "V08_CLAIMS_SUPPORTED"),
]


@pytest.mark.parametrize("fault, scenario, finding, check", FAULT_CASES)
def test_each_fault_is_caught_by_the_critic_and_by_the_gate(cases, runs, fault, scenario, finding, check):
    predicate = (lambda c: c.simulation.draft_faults and c.simulation.draft_faults[0] == ["ASSIGN_UNAVAILABLE_TECH"]) \
        if fault == "ASSIGN_UNAVAILABLE_TECH" else (lambda c: True)
    case = first_case(cases, scenario, predicate)
    values = runs[case.case_id]
    context = context_for(case, values)
    draft = apply_fault(clean_draft(context), fault, context)
    critique = review(case, values, draft)
    assert critique.verdict == "REVISE" and finding in {f.code for f in critique.findings}
    report = gate(values, draft)
    assert not report.passed and report.route == "revise" and check in {f.code for f in report.failures}


def test_promising_an_unavailable_part_is_caught(cases, runs):
    case = first_case(cases, "resource_unavailable", lambda c: c.exception.type == "PART_DELAY")
    values = runs[case.case_id]
    context = context_for(case, values)
    draft = apply_fault(clean_draft(context), "PROMISE_UNAVAILABLE_PART", context)
    codes = {f.code for f in review(case, values, draft).findings}
    assert {"PART_NOT_AVAILABLE", "UNREALISTIC_COMPLETION"} <= codes
    assert "V04_PARTS_PROMISE" in {f.code for f in gate(values, draft).failures}


def test_citing_a_quarantined_document_is_caught(cases, runs):
    case = first_case(cases, "prompt_injection", lambda c: c.equipment.failure_mode == "boiler_pressure_fault")
    values = runs[case.case_id]
    context = context_for(case, values)
    draft = apply_fault(clean_draft(context), "CITE_INJECTED_DOC", context)
    assert "UNTRUSTED_SOURCE_CITED" in {f.code for f in review(case, values, draft).findings}
    assert "V07_CITATIONS_VALID" in {f.code for f in gate(values, draft).failures}


def test_forbidden_action_blocks_whatever_the_draft_says(cases, runs):
    case = first_case(cases, "policy_violation")
    values = runs[case.case_id]
    draft = clean_draft(context_for(case, values))
    assert review(case, values, draft).verdict == "POLICY_VIOLATION"
    report = gate(values, draft, human={"decision": "approve", "draft_index": 0})
    assert report.route == "block", "not even a human approval waives a forbidden action"


def test_missing_evidence_escalates_instead_of_guessing(cases, runs):
    case = first_case(cases, "irrelevant_retrieval")
    values = runs[case.case_id]
    draft = clean_draft(context_for(case, values))
    assert review(case, values, draft).verdict == "INSUFFICIENT_EVIDENCE"
    assert gate(values, draft).route == "escalate"


def test_contradictions_and_cost_need_a_human_and_approval_satisfies_only_that(cases, runs):
    for scenario in ("contradictory_evidence", "human_escalation"):
        case = first_case(cases, scenario)
        values = runs[case.case_id]
        draft = clean_draft(context_for(case, values))
        assert review(case, values, draft).verdict == "HUMAN_REVIEW"
        assert gate(values, draft).route == "human_review"
        assert gate(values, draft, human={"decision": "approve", "draft_index": 0}).route == "respond"
        bad = apply_fault(draft, "UNKNOWN_TECHNICIAN", context_for(case, values))
        assert gate(values, bad, human={"decision": "approve", "draft_index": 0}).route == "revise"


def test_contradictions_are_detected_from_the_reported_facts(cases):
    case = first_case(cases, "contradictory_evidence")
    facts = compute_facts(case, make_deps().settings)
    assert facts.contradictions and facts.contradictions[0]["field"] == "unit_status"
    assert "CONTRADICTION" in facts.human_triggers and "POL-CONTRADICTION" in facts.required_policies


def test_human_approval_counts_only_for_the_reviewed_draft(cases):
    from exception_agents.agents import human_approval_for
    state = {"human_review": {"decision": "approve", "draft_index": 0}}
    assert human_approval_for(state, 0) and human_approval_for(state, 1) is None


def test_the_gate_catches_what_a_disabled_critic_lets_through(cases):
    """Defense in depth: with the critic switched off, faulty drafts still never reach a response."""
    deps = make_deps("critic_off")
    for case in cases.values():
        if case.scenario in ("critic_revision", "policy_incompatible_draft", "unsupported_response"):
            values = run_simulated(case, deps)
            assert values["critiques"][0]["verdict"] == "APPROVED"
            assert values["validations"][0]["passed"] is False
            final_draft = values["drafts"][-1]
            assert values["final"]["recommendation"]["technician_id"] == final_draft["technician_id"]
            assert values["validations"][-1]["passed"] is True
