"""Offline demo data (demo/data.js) built from real graph runs and real evaluation results.

Every case shown was executed through the LangGraph StateGraph in Demo Mode; human reviews in
these runs come from the case's scripted reviewer and are labelled as simulated.
"""

from __future__ import annotations

import json
from importlib import metadata
from pathlib import Path

from . import __version__
from .agents import CONTROLS
from .evaluator import GATES, mutation_self_test, score
from .harness import run_records


def versions() -> dict:
    found = {"langgraph-exception-agents": __version__}
    for package in ("langgraph", "langgraph-checkpoint", "langgraph-checkpoint-sqlite", "pydantic"):
        try:
            found[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            found[package] = "not installed"
    return found


def case_view(state: dict, scored: dict) -> dict:
    case = state["case"]
    research = state.get("research")
    return {
        "case_id": case["case_id"], "split": case["split"], "scenario": case["scenario"],
        "incident": {"type": case["exception"]["type"], "summary": case["exception"]["summary"],
                     "request": case["service_request"], "equipment": case["equipment"], "location": case["location"],
                     "technicians": case["technicians"], "parts": case["parts"], "competing_requests": case["competing_requests"],
                     "reported_facts": case["reported_facts"]},
        "trace": state["trace"],
        "intake": state.get("intake"),
        "research": None if research is None else {
            "query": research["query"], "insufficient": research["insufficient"], "reason": research["reason"],
            "hits": [{k: h[k] for k in ("chunk_id", "doc_id", "title", "category", "score", "flagged", "flag_reason", "snippet")}
                     for h in research["hits"]],
            "evidence": [h["chunk_id"] for h in research["evidence"]],
        },
        "analysis": None if not state.get("analysis") else {
            k: state["analysis"][k] for k in ("sla_status", "sla_deadline", "earliest_completion", "eligible_technicians",
                                              "policy_technician", "required_policies", "human_triggers", "contradictions",
                                              "conflicts", "forbidden_requested_action", "technicians")},
        "drafts": state.get("drafts", []), "critiques": state.get("critiques", []),
        "validations": state.get("validations", []), "human_review": state.get("human_review"),
        "errors": state.get("errors", []), "final": state["final"],
        "evaluation": {"checks": scored["checks"], "expected_outcome": scored["expected_outcome"],
                       "unsupported_claims": scored["unsupported_claims"]},
    }


def build_demo_data() -> dict:
    records, states = run_records("all", "none")
    report_all = score(records, "all")
    by_split = {}
    for split in ("dev", "holdout"):
        subset = [r for r in records if r["case_id"].startswith("EXC-DEV" if split == "dev" else "EXC-HO")]
        result = score(subset, split)
        by_split[split] = {"metrics": result["metrics"], "counts": result["counts"], "passed": result["passed"]}
    controls = []
    for name in CONTROLS:
        if name == "none":
            continue
        control_records, _ = run_records("all", name)
        result = score(control_records, "all")
        controls.append({"control": name, "passed": result["passed"], "metrics": result["metrics"],
                         "failed_gates": [g for g, ok in result["gates"].items() if not ok]})
    per_case = {c["case_id"]: c for c in report_all["per_case"]}
    return {
        "project": "langgraph-exception-agents",
        "mode": "Demo Mode: real LangGraph workflow, scripted reasoner (no language model), simulated reviewers.",
        "company": "Quellmark Field Service (fictional)",
        "versions": versions(),
        "gates": GATES,
        "evaluation": by_split,
        "controls": controls,
        "mutation_self_test": mutation_self_test(records, "all"),
        "cases": [case_view(state, per_case[state["case"]["case_id"]]) for state in states],
    }


def render(data: dict) -> str:
    return "window.AGENT_DEMO = " + json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + ";\n"


def write_or_check(path: Path, check: bool) -> tuple[bool, str]:
    text = render(build_demo_data())
    if check:
        if not path.exists():
            return False, f"{path} does not exist"
        same = path.read_text(encoding="utf-8").replace("\r\n", "\n") == text
        return same, f"{path} is up to date" if same else f"{path} differs from a fresh run"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return True, f"wrote {path} ({len(text):,} bytes)"
