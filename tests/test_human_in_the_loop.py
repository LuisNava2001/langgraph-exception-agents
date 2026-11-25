"""Interrupt, persist, resume: LangGraph checkpointing with SQLite across a simulated restart."""

import sqlite3

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from exception_agents import agents
from exception_agents import graph as g
from exception_agents.runner import (
    NothingToResume,
    ReviewLog,
    config_for,
    open_checkpointer,
    pending_interrupt,
    resume,
    start,
)
from exception_agents.config import InputError

from .conftest import first_case


@pytest.fixture()
def cost_case(cases):
    return first_case(cases, "human_escalation", lambda c: c.exception.type == "COST_APPROVAL")


def test_interrupt_pauses_with_a_reviewable_payload(deps, cost_case):
    graph = g.build_graph(deps, InMemorySaver())
    result = start(graph, cost_case, "t1", deps)
    assert result.status == "AWAITING_REVIEW" and "final" not in result.values
    payload = result.interrupt["value"]
    assert payload["triggers"] == ["COST_APPROVAL_REQUIRED"] and payload["allowed_decisions"] == ["approve", "reject"]
    assert payload["draft"]["estimated_cost"] > 2500
    assert graph.get_state(config_for("t1", deps)).next == ("human_review",)


@pytest.mark.parametrize("decision, outcome", [("approve", "RESOLVED_AFTER_REVIEW"), ("reject", "REJECTED")])
def test_resume_with_a_decision(deps, cost_case, decision, outcome):
    graph = g.build_graph(deps, InMemorySaver())
    start(graph, cost_case, "t2", deps)
    result = resume(graph, "t2", {"decision": decision, "reviewer_id": "reviewer-a", "note": "n"}, deps)
    values = result.values
    assert result.status == "COMPLETED" and values["outcome"] == outcome
    assert values["human_review"] == {"decision": decision, "reviewer_id": "reviewer-a", "note": "n", "simulated": False,
                                      "triggers": ["COST_APPROVAL_REQUIRED"], "draft_index": 0}
    assert values["final"]["human_review"]["reviewer_id"] == "reviewer-a"


def test_state_survives_a_restart_with_sqlite(deps, cost_case, tmp_path):
    db = tmp_path / "checkpoints.sqlite"
    saver = open_checkpointer(db)
    start(g.build_graph(deps, saver), cost_case, "persisted", deps)
    saver.conn.close()  # the "process" ends here
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id = 'persisted'").fetchone()[0] > 0
    saver2 = open_checkpointer(db)  # a new process opens the same file
    graph2 = g.build_graph(agents.make_deps(), saver2)
    assert pending_interrupt(graph2, config_for("persisted", deps))["node"] == "human_review"
    result = resume(graph2, "persisted", {"decision": "approve", "reviewer_id": "r2"}, deps)
    assert result.values["outcome"] == "RESOLVED_AFTER_REVIEW"
    assert [t["node"] for t in result.values["trace"]][-3:] == ["human_review", "validate", "respond"]
    saver2.conn.close()


def test_resume_is_idempotent(deps, cost_case):
    graph = g.build_graph(deps, InMemorySaver())
    start(graph, cost_case, "t3", deps)
    first = resume(graph, "t3", {"decision": "approve", "reviewer_id": "r"}, deps).values
    with pytest.raises(NothingToResume):
        resume(graph, "t3", {"decision": "reject", "reviewer_id": "someone-else"}, deps)
    assert graph.get_state(config_for("t3", deps)).values == first, "a second decision changes nothing"


def test_the_review_node_reruns_on_resume_so_it_has_no_side_effects_before_interrupt(deps, cost_case):
    calls = []

    def counting_review(state, deps):
        calls.append(1)
        return agents.human_review(state, deps)

    graph = g.build_graph(deps, InMemorySaver(), nodes=dict(g.NODES, human_review=counting_review))
    start(graph, cost_case, "t4", deps)
    resume(graph, "t4", {"decision": "approve", "reviewer_id": "r"}, deps)
    assert len(calls) == 2, "LangGraph restarts the interrupted node from the top"


def test_invalid_decisions_are_rejected_and_escalate(deps, cost_case):
    graph = g.build_graph(deps, InMemorySaver())
    start(graph, cost_case, "t5", deps)
    values = resume(graph, "t5", {"decision": "maybe", "reviewer_id": "r"}, deps).values
    assert values["outcome"] == "ESCALATED" and values["errors"][0]["node"] == "human_review"


def test_thread_ids_are_not_reused_and_unknown_threads_fail(deps, cost_case):
    graph = g.build_graph(deps, InMemorySaver())
    start(graph, cost_case, "t6", deps)
    with pytest.raises(InputError, match="already exists"):
        start(graph, cost_case, "t6", deps)
    with pytest.raises(InputError, match="unknown thread"):
        resume(graph, "nope", {"decision": "approve", "reviewer_id": "r"}, deps)


def test_review_log_records_each_interrupt_once(tmp_path):
    log = ReviewLog(tmp_path / "reviews.sqlite")
    decision = {"decision": "approve", "reviewer_id": "r", "note": ""}
    assert log.record("thread", "interrupt-1", "case", decision) is True
    assert log.record("thread", "interrupt-1", "case", decision | {"decision": "reject"}) is False
    assert [e["decision"] for e in log.entries("thread")] == ["approve"]
    log.close()
