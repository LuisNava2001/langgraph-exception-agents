"""Evaluation harness: reproducible, independent of the agents, and able to fail."""

import ast
import inspect

import pytest

from exception_agents import evaluator
from exception_agents.harness import evaluate, run_records


def test_records_are_reproducible():
    assert run_records("dev")[0] == run_records("dev")[0]


@pytest.mark.parametrize("split, cases", [("dev", 20), ("holdout", 37), ("all", 57)])
def test_gates_pass_on_every_split(split, cases):
    report = evaluate(split)
    assert report["counts"]["cases"] == cases
    assert report["passed"], {k: v for k, v in report["metrics"].items()}
    assert report["metrics"]["unsupported_claims"] == 0 and report["metrics"]["critic_false_alarms"] == 0


@pytest.mark.parametrize("control, must_fail", [
    ("critic_and_validator_off", {"policy_compliance", "end_to_end_accuracy", "evidence_grounding", "critic_detection"}),
    ("retrieval_off", {"retrieval_accuracy", "end_to_end_accuracy", "routing_accuracy"}),
    ("injection_filter_off", {"injection_containment", "end_to_end_accuracy"}),
    ("critic_off", {"critic_detection", "routing_accuracy"}),
])
def test_negative_controls_fail_the_gates(control, must_fail):
    report = evaluate("all", control)
    failed = {gate for gate, ok in report["gates"].items() if not ok}
    assert not report["passed"] and must_fail <= failed, failed


def test_with_only_the_critic_off_outcomes_stay_correct():
    """The deterministic gate still protects every released recommendation."""
    metrics = evaluate("all", "critic_off")["metrics"]
    assert metrics["policy_compliance"] == metrics["end_to_end_accuracy"] == 1.0


def test_the_validator_alone_is_redundant_on_this_dataset():
    """Reported as is: the critic catches every injected fault, so switching off only the gate changes nothing here."""
    assert evaluate("all", "validator_off")["passed"]


def test_the_scorer_detects_every_deliberate_mutation(records):
    results = evaluator.mutation_self_test(records, "all")
    assert len(results) == 8 and all(r["detected"] for r in results), results


def test_scorer_requires_the_same_case_set(records):
    with pytest.raises(ValueError):
        evaluator.score(records[:-1], "all")


def test_scorer_does_not_import_the_agents():
    tree = ast.parse(inspect.getsource(evaluator))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} | \
        {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    forbidden = {"agents", "critic", "validation", "rules", "reasoners", "graph", "runner", "retrieval", "response"}
    assert not {m.split(".")[-1] for m in imported if m} & forbidden, imported


def test_ground_truth_labels_are_independent_of_agent_output(truth):
    labels = {key for gt in truth.values() for key in gt}
    assert {"allowed_technicians", "expected_sla_status", "required_policies", "relevant_docs", "expected_findings"} <= labels
    unknown_tech = [gt for gt in truth.values() if gt["expected_outcome"].startswith("RESOLVED") and not gt["allowed_technicians"]]
    assert not unknown_tech, "every released case names its acceptable technicians"
