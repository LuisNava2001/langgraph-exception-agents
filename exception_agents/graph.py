"""The LangGraph StateGraph: typed shared state, field ownership, routing and limits.

Field ownership: every node may write only the state fields listed in ``NODE_WRITES``. The
wrapper rejects any other key and validates every owned value against its Pydantic model, so a
misbehaving agent cannot overwrite, for example, ``outcome`` or ``final``. A rejected update, or
any exception, is recorded in ``errors`` and the graph routes to ``escalate``.
"""

from __future__ import annotations

import operator
from typing import Annotated, Callable

from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from . import agents
from .agents import Deps
from .models import (
    AnalysisResult,
    Critique,
    Draft,
    FinalResponse,
    HumanReviewRecord,
    IntakeResult,
    ResearchResult,
    ValidationReport,
)


class GraphState(TypedDict, total=False):
    case: dict                                    # input; no node may write it
    intake: dict
    research: dict
    analysis: dict
    draft: dict
    drafts: Annotated[list, operator.add]         # append-only history of drafts
    critique: dict
    critiques: Annotated[list, operator.add]
    validation: dict
    validations: Annotated[list, operator.add]
    human_review: dict
    final: dict
    outcome: str
    errors: Annotated[list, operator.add]
    trace: Annotated[list, operator.add]          # one entry per node execution


NODE_WRITES = {
    "intake": {"intake"},
    "research": {"research"},
    "analysis": {"analysis", "draft", "drafts"},
    "critic": {"critique", "critiques"},
    "validate": {"validation", "validations"},
    "human_review": {"human_review"},
    "respond": {"final", "outcome"},
    "escalate": {"final", "outcome"},
    "block": {"final", "outcome"},
}
SLICE_MODELS = {
    "intake": IntakeResult, "research": ResearchResult, "analysis": AnalysisResult, "draft": Draft,
    "critique": Critique, "validation": ValidationReport, "human_review": HumanReviewRecord, "final": FinalResponse,
}
LIST_MODELS = {"drafts": Draft, "critiques": Critique, "validations": ValidationReport}
OUTCOMES = {"RESOLVED", "RESOLVED_AFTER_REVIEW", "ESCALATED", "BLOCKED", "REJECTED"}


class FieldOwnershipError(RuntimeError):
    pass


class SimulatedAgentFailure(RuntimeError):
    """Raised on purpose when a case's simulation block names this node (agent-error scenario)."""


def check_update(name: str, update: dict) -> dict:
    """Enforce field ownership and the schema of every written value."""
    extra = set(update) - NODE_WRITES[name]
    if extra:
        raise FieldOwnershipError(f"{name} may not write {sorted(extra)}")
    clean = {}
    for key, value in update.items():
        if key in SLICE_MODELS:
            clean[key] = SLICE_MODELS[key].model_validate(value).model_dump(mode="json")
        elif key in LIST_MODELS:
            clean[key] = [LIST_MODELS[key].model_validate(v).model_dump(mode="json") for v in value]
        elif key == "outcome":
            if value not in OUTCOMES:
                raise FieldOwnershipError(f"invalid outcome {value!r}")
            clean[key] = value
    if "outcome" in clean and clean["outcome"] != clean.get("final", {}).get("outcome"):
        raise FieldOwnershipError("outcome must equal final.outcome")
    return clean


def guarded(name: str, fn: Callable, deps: Deps, enforce: bool = True) -> Callable:
    def node(state: dict) -> dict:
        step = len(state.get("trace", [])) + 1
        try:
            if (state["case"].get("simulation") or {}).get("fail_node") == name:
                raise SimulatedAgentFailure(f"simulated failure in {name}")
            update = fn(state, deps)
            summary = update.pop("_summary", "")
            update = check_update(name, update) if enforce else update
        except GraphInterrupt:
            raise  # LangGraph's interrupt mechanism must propagate untouched
        except Exception as exc:  # noqa: BLE001 - any agent failure becomes a recorded error
            error = {"node": name, "type": type(exc).__name__, "message": str(exc)[:300]}
            return {"errors": [error], "trace": [{"step": step, "node": name, "status": "error",
                                                  "summary": f"{error['type']}: {error['message']}"}]}
        update["trace"] = [{"step": step, "node": name, "status": "ok", "summary": summary}]
        return update
    node.__name__ = name
    return node


# --- routing ---------------------------------------------------------------------------------

def revisions_left(state: dict, max_revisions: int) -> bool:
    return len(state.get("drafts", [])) - 1 < max_revisions


def after_intake(state: dict) -> str:
    if state.get("errors") or (state.get("intake") or {}).get("status") != "READY":
        return "escalate"
    return "research"


def after_step(state: dict, next_node: str) -> str:
    return "escalate" if state.get("errors") else next_node


def after_critic(state: dict, max_revisions: int) -> str:
    if state.get("errors"):
        return "escalate"
    verdict = state["critique"]["verdict"]
    if verdict == "APPROVED":
        return "validate"
    if verdict == "REVISE":
        return "analysis" if revisions_left(state, max_revisions) else "escalate"
    if verdict == "HUMAN_REVIEW":
        return "human_review"
    if verdict == "POLICY_VIOLATION":
        return "block"
    return "escalate"  # INSUFFICIENT_EVIDENCE


def after_human(state: dict) -> str:
    if state.get("errors"):
        return "escalate"
    return "validate" if state["human_review"]["decision"] == "approve" else "escalate"


def after_validate(state: dict, max_revisions: int) -> str:
    if state.get("errors"):
        return "escalate"
    route = state["validation"]["route"]
    if route == "revise":
        return "analysis" if revisions_left(state, max_revisions) else "escalate"
    return {"respond": "respond", "human_review": "human_review", "block": "block"}.get(route, "escalate")


NODES = {
    "intake": agents.intake, "research": agents.research, "analysis": agents.analysis, "critic": agents.critic,
    "validate": agents.validate, "human_review": agents.human_review, "respond": agents.respond,
    "escalate": agents.escalate, "block": agents.block,
}


def build_graph(deps: Deps, checkpointer=None, enforce_ownership: bool = True, nodes: dict | None = None):
    max_revisions = deps.settings.max_revisions
    builder = StateGraph(GraphState)
    for name, fn in (nodes or NODES).items():
        builder.add_node(name, guarded(name, fn, deps, enforce_ownership))
    def route_research(state: dict) -> str:
        return after_step(state, "analysis")

    def route_analysis(state: dict) -> str:
        return after_step(state, "critic")

    def route_critic(state: dict) -> str:
        return after_critic(state, max_revisions)

    def route_validate(state: dict) -> str:
        return after_validate(state, max_revisions)

    builder.add_edge(START, "intake")
    builder.add_conditional_edges("intake", after_intake, {"research": "research", "escalate": "escalate"})
    builder.add_conditional_edges("research", route_research, {"analysis": "analysis", "escalate": "escalate"})
    builder.add_conditional_edges("analysis", route_analysis, {"critic": "critic", "escalate": "escalate"})
    builder.add_conditional_edges("critic", route_critic,
                                  {"validate": "validate", "analysis": "analysis", "human_review": "human_review",
                                   "block": "block", "escalate": "escalate"})
    builder.add_conditional_edges("human_review", after_human, {"validate": "validate", "escalate": "escalate"})
    builder.add_conditional_edges("validate", route_validate,
                                  {"respond": "respond", "analysis": "analysis", "human_review": "human_review",
                                   "block": "block", "escalate": "escalate"})
    for terminal in ("respond", "escalate", "block"):
        builder.add_edge(terminal, END)
    return builder.compile(checkpointer=checkpointer)
