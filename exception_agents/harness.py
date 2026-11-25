"""Evaluation harness: runs every case through the real graph and extracts plain records.

The scorer (evaluator.py) only sees these records, never the agents' internals.
"""

from __future__ import annotations

from .agents import CONTROLS, make_deps
from .evaluator import mutation_self_test, score
from .runner import load_cases, run_simulated


def record_from_state(values: dict) -> dict:
    final = values["final"]
    research = values.get("research")
    return {
        "case_id": values["case"]["case_id"],
        "path": [step["node"] for step in values["trace"]],
        "verdicts": [c["verdict"] for c in values.get("critiques", [])],
        "critic_findings": [[f["code"] for f in c["findings"]] for c in values.get("critiques", [])],
        "outcome": final["outcome"],
        "escalation_reason": (final.get("escalation") or {}).get("reason"),
        "recommendation": final.get("recommendation"),
        "citations": final.get("citations", []),
        "claims": final.get("claims", []),
        "retrieved_docs": list(dict.fromkeys(h["doc_id"] for h in research["hits"])) if research else [],
        "evidence_chunks": [h["chunk_id"] for h in research["evidence"]] if research else [],
        "flagged_docs": sorted({h["doc_id"] for h in research["flagged"]}) if research else [],
        "insufficient": research["insufficient"] if research else None,
        "human_review": final.get("human_review"),
        "errors": values.get("errors", []),
    }


def run_records(split: str = "all", controls: str = "none") -> tuple[list[dict], list[dict]]:
    """Return (records for scoring, full final states for the demo)."""
    if controls not in CONTROLS:
        raise ValueError(f"unknown control {controls!r}; choose from {sorted(CONTROLS)}")
    deps = make_deps(controls)
    states = [run_simulated(case, deps) for case in load_cases(split)]
    return [record_from_state(s) for s in states], states


def evaluate(split: str = "all", controls: str = "none", self_test: bool = False) -> dict:
    records, _ = run_records(split, controls)
    report = score(records, split)
    report["controls"] = controls
    if self_test:
        report["mutation_self_test"] = mutation_self_test(records, split)
    return report
