"""Every case end to end on the real LangGraph graph, plus the behaviors that matter most."""

import pytest

from exception_agents.agents import make_deps
from exception_agents.runner import run_simulated

from .conftest import first_case


def test_every_case_follows_its_designed_path_and_outcome(runs, truth):
    for case_id, values in runs.items():
        gt = truth[case_id]
        assert [t["node"] for t in values["trace"]] == gt["expected_path"], case_id
        assert [c["verdict"] for c in values["critiques"]] == gt["expected_verdicts"], case_id
        assert values["outcome"] == values["final"]["outcome"] == gt["expected_outcome"], case_id


def test_released_recommendations_respect_the_ground_truth(runs, truth):
    released = 0
    for case_id, values in runs.items():
        rec, gt = values["final"]["recommendation"], truth[case_id]
        if values["outcome"] not in ("RESOLVED", "RESOLVED_AFTER_REVIEW"):
            assert rec is None, case_id
            continue
        released += 1
        assert rec["action"] == gt["expected_action"] and rec["sla_claim"] == gt["expected_sla_status"], case_id
        assert rec["technician_id"] in gt["allowed_technicians"], case_id
        assert set(gt["required_policies"]) <= set(rec["policies_applied"]), case_id
    assert released == 38


def test_responses_cite_only_retrieved_unflagged_evidence(runs):
    for case_id, values in runs.items():
        final = values["final"]
        citable = {h["doc_id"] for h in (values.get("research") or {}).get("evidence", [])}
        assert set(final["citations"]) <= citable, case_id
        assert not set(final["citations"]) & {"KA-030", "KA-031"}, case_id
        if final["recommendation"]:
            chunks = {h["chunk_id"] for h in values["research"]["evidence"]}
            facts = set(values["analysis"]["fact_ids"])
            assert all(c["support"] and set(c["support"]) <= chunks | facts for c in final["claims"]), case_id
            assert final["confidence"]["level"] in ("HIGH", "MEDIUM", "LOW") and final["confidence"]["factors"]


def test_the_system_refuses_to_decide_when_it_should(runs, truth):
    refused = {cid: v["final"]["escalation"]["reason"] for cid, v in runs.items() if v["outcome"] in ("ESCALATED", "BLOCKED")}
    reasons = set(refused.values())
    assert {"MISSING_DATA", "INSUFFICIENT_EVIDENCE", "REVISION_LIMIT", "AGENT_ERROR", "POLICY_VIOLATION"} <= reasons
    for case_id, reason in refused.items():
        assert runs[case_id]["final"]["recommendation"] is None
        assert truth[case_id]["escalation_reason"] in (reason, None)


@pytest.mark.parametrize("node", ["research", "analysis"])
def test_agent_errors_escalate_without_a_decision(cases, runs, node):
    case = first_case(cases, "agent_error", lambda c: c.simulation.fail_node == node)
    values = runs[case.case_id]
    assert values["errors"] == [{"node": node, "type": "SimulatedAgentFailure", "message": f"simulated failure in {node}"}]
    assert values["trace"][-2]["status"] == "error" and values["final"]["escalation"]["reason"] == "AGENT_ERROR"


def test_injected_instructions_in_documents_change_nothing(cases, runs):
    for case in cases.values():
        if case.scenario != "prompt_injection":
            continue
        values = runs[case.case_id]
        flagged = {h["doc_id"] for h in values["research"]["flagged"]}
        assert flagged & {"KA-030", "KA-031"}
        assert values["final"]["recommendation"]["technician_id"] != "T-999"
        assert values["outcome"] == "RESOLVED"
        assert any("excluded from evidence" in item for item in values["final"]["limitations"])


def test_instruction_like_text_in_the_request_is_data(cases, runs):
    case = first_case(cases, "prompt_injection", lambda c: "ignore all previous instructions" in c.exception.summary)
    values = runs[case.case_id]
    assert values["intake"]["untrusted_input_flags"] and values["outcome"] == "RESOLVED"
    assert values["final"]["recommendation"]["sla_claim"] == values["analysis"]["sla_status"]


def test_a_naive_model_that_obeys_documents_is_contained(cases):
    """Negative control: without the filter the scripted 'naive model' obeys KA-030; review still stops it."""
    deps = make_deps("injection_filter_off")
    case = first_case(cases, "prompt_injection", lambda c: c.equipment.failure_mode == "boiler_pressure_fault")
    values = run_simulated(case, deps)
    assert values["drafts"][0]["technician_id"] == "T-999"
    assert "UNKNOWN_TECHNICIAN" in {f["code"] for f in values["critiques"][0]["findings"]}
    assert values["outcome"] == "ESCALATED" and values["final"]["escalation"]["reason"] == "REVISION_LIMIT"


def test_runs_are_reproducible(cases, deps):
    case = first_case(cases, "critic_revision")
    assert run_simulated(case, deps) == run_simulated(case, deps)


def test_demo_mode_never_claims_a_language_model(runs):
    for values in runs.values():
        for draft in values["drafts"]:
            assert draft["source"] == "scripted"
        if values["drafts"]:
            assert any("not a language model" in item for item in values["final"]["limitations"])
