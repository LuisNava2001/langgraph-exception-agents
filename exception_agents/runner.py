"""Running cases through the graph.

* Evaluation and demo runs use an in-memory checkpointer and answer interrupts with the case's
  scripted reviewer (marked ``simulated``).
* The CLI uses a SQLite checkpointer so a run can stop at a human-review interrupt, the process
  can exit, and a later ``review`` command can resume the same thread.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .agents import Deps
from .config import PACKAGE_DATA, InputError
from .graph import build_graph
from .models import ExceptionCase


@dataclass
class RunResult:
    status: str  # COMPLETED | AWAITING_REVIEW
    thread_id: str
    values: dict
    interrupt: dict | None


class NothingToResume(InputError):
    pass


def load_cases(split: str | None = None) -> list[ExceptionCase]:
    splits = ["dev", "holdout"] if split in (None, "all") else [split]
    cases = []
    for name in splits:
        data = json.loads((PACKAGE_DATA / "cases" / f"{name}.json").read_text(encoding="utf-8"))
        cases += [ExceptionCase.model_validate(c) for c in data["cases"]]
    return cases


def get_case(case_id: str) -> ExceptionCase:
    for case in load_cases("all"):
        if case.case_id == case_id:
            return case
    raise InputError(f"unknown case {case_id!r}; list them with 'cases'")


def initial_state(case: ExceptionCase) -> dict:
    return {"case": case.model_dump(mode="json"), "drafts": [], "critiques": [], "validations": [], "errors": [], "trace": []}


def config_for(thread_id: str, deps: Deps) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": deps.settings.recursion_limit}


def pending_interrupt(graph, config: dict) -> dict | None:
    snapshot = graph.get_state(config)
    for task in snapshot.tasks:
        for item in task.interrupts:
            return {"id": item.id, "node": task.name, "value": item.value}
    return None


def _result(graph, config: dict) -> RunResult:
    snapshot = graph.get_state(config)
    waiting = pending_interrupt(graph, config)
    return RunResult("AWAITING_REVIEW" if waiting else "COMPLETED", config["configurable"]["thread_id"],
                     snapshot.values, waiting)


def start(graph, case: ExceptionCase, thread_id: str, deps: Deps) -> RunResult:
    config = config_for(thread_id, deps)
    if graph.get_state(config).values:
        raise InputError(f"thread {thread_id!r} already exists; use 'show' or 'review', or pick another --thread")
    graph.invoke(initial_state(case), config, durability="sync")
    return _result(graph, config)


def resume(graph, thread_id: str, decision: dict, deps: Deps) -> RunResult:
    config = config_for(thread_id, deps)
    if not graph.get_state(config).values:
        raise InputError(f"unknown thread {thread_id!r}")
    if pending_interrupt(graph, config) is None:
        raise NothingToResume(f"thread {thread_id!r} has no pending review")
    graph.invoke(Command(resume=decision), config, durability="sync")
    return _result(graph, config)


def run_simulated(case: ExceptionCase, deps: Deps) -> dict:
    """Run to completion in memory; answer a review interrupt with the case's scripted reviewer."""
    graph = build_graph(deps, InMemorySaver())
    result = start(graph, case, case.case_id, deps)
    if result.status == "AWAITING_REVIEW":
        script = case.simulation.reviewer
        if script is None:
            raise InputError(f"{case.case_id} paused for review but defines no simulated reviewer")
        result = resume(graph, case.case_id, script.model_dump() | {"simulated": True}, deps)
        if result.status != "COMPLETED":
            raise InputError(f"{case.case_id} paused twice; the simulated reviewer answers once")
    return result.values


def open_checkpointer(path: Path) -> SqliteSaver:
    path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(str(path), check_same_thread=False))


class ReviewLog:
    """Append-only record of human decisions taken through the CLI (one row per interrupt)."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS review_log (thread_id TEXT, interrupt_id TEXT, case_id TEXT, reviewer_id TEXT,"
            " decision TEXT, note TEXT, recorded_at TEXT, PRIMARY KEY (thread_id, interrupt_id))")

    def record(self, thread_id: str, interrupt_id: str, case_id: str, decision: dict) -> bool:
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO review_log VALUES (?, ?, ?, ?, ?, ?, ?)",
            (thread_id, interrupt_id, case_id, decision["reviewer_id"], decision["decision"], decision.get("note", ""),
             datetime.now(timezone.utc).isoformat(timespec="seconds")))
        self.conn.commit()
        return cursor.rowcount == 1

    def entries(self, thread_id: str) -> list[dict]:
        rows = self.conn.execute("SELECT thread_id, interrupt_id, case_id, reviewer_id, decision, note, recorded_at "
                                 "FROM review_log WHERE thread_id = ? ORDER BY recorded_at", (thread_id,)).fetchall()
        keys = ("thread_id", "interrupt_id", "case_id", "reviewer_id", "decision", "note", "recorded_at")
        return [dict(zip(keys, row)) for row in rows]

    def close(self) -> None:
        self.conn.close()
