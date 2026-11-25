"""StateGraph structure, typed state, field ownership, routing rules and iteration limits."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from pydantic import ValidationError

from exception_agents import graph as g
from exception_agents.models import Draft, HumanDecision, IntakeResult
from exception_agents.runner import config_for, initial_state, run_simulated, start

from .conftest import first_case


def test_graph_compiles_with_the_expected_nodes_and_edges(deps):
    compiled = g.build_graph(deps, InMemorySaver())
    drawable = compiled.get_graph()
    assert set(drawable.nodes) == {"__start__", "__end__", *g.NODES}
    edges = {(e.source, e.target) for e in drawable.edges}
    assert ("__start__", "intake") in edges
    for source, targets in {"critic": {"validate", "analysis", "human_review", "block", "escalate"},
                            "validate": {"respond", "analysis", "human_review", "block", "escalate"},
                            "intake": {"research", "escalate"}, "human_review": {"validate", "escalate"}}.items():
        assert {t for s, t in edges if s == source} == targets, source
    assert {s for s, t in edges if t == "__end__"} == {"respond", "escalate", "block"}


def test_state_slices_are_validated():
    with pytest.raises(ValidationError):
        IntakeResult.model_validate({"case_id": "x"})
    with pytest.raises(ValidationError):
        HumanDecision.model_validate({"decision": "maybe", "reviewer_id": "r"})
    with pytest.raises(ValidationError):
        Draft.model_validate({"action": "LAUNCH_ROCKET"})


@pytest.mark.parametrize("node, update, message", [
    ("analysis", {"outcome": "RESOLVED"}, "may not write"),
    ("critic", {"final": {}}, "may not write"),
    ("intake", {"case": {}}, "may not write"),
    ("respond", {"outcome": "DONE", "final": {}}, "Field required|invalid outcome"),
])
def test_field_ownership_is_enforced(node, update, message):
    with pytest.raises(Exception, match=message):
        g.check_update(node, update)


def test_a_rogue_agent_cannot_set_the_outcome(deps, cases):
    def rogue_research(state, deps):
        return {"research": state.get("research"), "outcome": "RESOLVED", "_summary": "tries to decide"}

    nodes = dict(g.NODES, research=rogue_research)
    compiled = g.build_graph(deps, InMemorySaver(), nodes=nodes)
    case = first_case(cases, "correct_resolution")
    result = start(compiled, case, "rogue", deps)
    values = result.values
    assert values["outcome"] == "ESCALATED" and values["final"]["escalation"]["reason"] == "AGENT_ERROR"
    assert values["errors"][0]["type"] == "FieldOwnershipError"
    assert [t["node"] for t in values["trace"]] == ["intake", "research", "escalate"]


def test_without_the_ownership_guard_the_rogue_update_would_land(deps, cases):
    """Shows what the guard prevents (negative control for the test above)."""
    def rogue_research(state, deps):
        from exception_agents.agents import research
        return research(state, deps) | {"outcome": "RESOLVED"}

    compiled = g.build_graph(deps, InMemorySaver(), enforce_ownership=False, nodes=dict(g.NODES, research=rogue_research))
    case = first_case(cases, "agent_error", lambda c: c.simulation.fail_node == "analysis")
    values = start(compiled, case, "unguarded", deps).values
    assert values["errors"] and values["outcome"] == "ESCALATED"  # the terminal node overwrote it later...
    snapshot_outcomes = [s.values.get("outcome") for s in compiled.get_state_history(config_for("unguarded", deps))]
    assert "RESOLVED" in snapshot_outcomes  # ...but the rogue value was in the state in between


def state_with(**fields):
    base = {"drafts": [{}], "errors": []}
    base.update(fields)
    return base


@pytest.mark.parametrize("verdict, drafts, expected", [
    ("APPROVED", 1, "validate"), ("REVISE", 1, "analysis"), ("REVISE", 2, "analysis"), ("REVISE", 3, "escalate"),
    ("HUMAN_REVIEW", 1, "human_review"), ("POLICY_VIOLATION", 1, "block"), ("INSUFFICIENT_EVIDENCE", 1, "escalate"),
])
def test_critic_routing_and_revision_budget(verdict, drafts, expected):
    state = state_with(critique={"verdict": verdict}, drafts=[{}] * drafts)
    assert g.after_critic(state, max_revisions=2) == expected


@pytest.mark.parametrize("route, drafts, expected", [
    ("respond", 1, "respond"), ("revise", 1, "analysis"), ("revise", 3, "escalate"), ("human_review", 1, "human_review"),
    ("block", 1, "block"), ("escalate", 1, "escalate"),
])
def test_validation_routing(route, drafts, expected):
    assert g.after_validate(state_with(validation={"route": route}, drafts=[{}] * drafts), max_revisions=2) == expected


def test_errors_always_route_to_escalate():
    state = state_with(errors=[{"node": "x"}], critique={"verdict": "APPROVED"}, validation={"route": "respond"},
                       human_review={"decision": "approve"}, intake={"status": "READY"})
    assert g.after_intake(state) == g.after_step(state, "critic") == "escalate"
    assert g.after_critic(state, 2) == g.after_validate(state, 2) == g.after_human(state) == "escalate"


def test_recursion_limit_is_a_hard_backstop(deps, cases):
    compiled = g.build_graph(deps, InMemorySaver())
    case = first_case(cases, "revision_limit")
    config = config_for("tiny", deps) | {"recursion_limit": 5}
    with pytest.raises(GraphRecursionError):
        compiled.invoke(initial_state(case), config)


def test_revision_budget_stops_a_draft_that_never_improves(deps, cases):
    case = first_case(cases, "revision_limit")
    values = run_simulated(case, deps)
    assert len(values["drafts"]) == deps.settings.max_revisions + 1
    assert [c["verdict"] for c in values["critiques"]] == ["REVISE"] * 3
    assert values["final"]["escalation"]["reason"] == "REVISION_LIMIT" and values["final"]["recommendation"] is None
