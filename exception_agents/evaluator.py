"""Independent scorer: compares run records with the structured ground truth.

Deliberately separate from the agents: it imports none of the agent, critic, validation, rules
or reasoner modules (a test enforces this). It does not recompute eligibility, SLA or policy
requirements; it checks recommendations against the labels written by the case generator and
grounds citations against the knowledge-base file and the case snapshot.
"""

from __future__ import annotations

import copy
import json
from importlib import resources

DATA = resources.files("exception_agents") / "data"
GATES = {
    "routing_accuracy": 0.95, "retrieval_accuracy": 0.90, "policy_compliance": 1.0, "critic_detection": 0.95,
    "evidence_grounding": 1.0, "escalation_accuracy": 0.95, "end_to_end_accuracy": 0.95, "injection_containment": 1.0,
}
MAX_UNSUPPORTED_CLAIMS = 0
RELEASED = ("RESOLVED", "RESOLVED_AFTER_REVIEW")


def load_truth(split: str) -> dict:
    splits = ["dev", "holdout"] if split == "all" else [split]
    truth = {}
    for name in splits:
        truth.update({t["case_id"]: t for t in json.loads((DATA / "ground_truth" / f"{name}.json").read_text(encoding="utf-8"))["truth"]})
    return truth


def load_case_snapshots(split: str) -> dict:
    splits = ["dev", "holdout"] if split == "all" else [split]
    cases = {}
    for name in splits:
        cases.update({c["case_id"]: c for c in json.loads((DATA / "cases" / f"{name}.json").read_text(encoding="utf-8"))["cases"]})
    return cases


def kb_doc_ids() -> set[str]:
    return {a["doc_id"] for a in json.loads((DATA / "knowledge_base.json").read_text(encoding="utf-8"))["articles"]}


def policy_ids() -> set[str]:
    return set(json.loads((DATA / "policies.json").read_text(encoding="utf-8"))["policies"])


def valid_fact_ids(case: dict) -> set[str]:
    ids = {"fact:request", "fact:sla"}
    ids |= {f"fact:technician:{t['technician_id']}" for t in case["technicians"]}
    ids |= {f"fact:part:{p['part_id']}" for p in case["parts"]}
    ids |= {f"fact:policy:{p}" for p in policy_ids()}
    return ids


def outcome_class(outcome: str, human_reviewed: bool) -> str:
    if outcome == "BLOCKED":
        return "BLOCK"
    if outcome == "ESCALATED":
        return "ESCALATE"
    if human_reviewed or outcome in ("RESOLVED_AFTER_REVIEW", "REJECTED"):
        return "HUMAN_REVIEW"
    return "AUTO_RESOLVE"


def score_case(record: dict, truth: dict, case: dict, kb_ids: set[str]) -> dict:
    released = record["outcome"] in RELEASED and record.get("recommendation")
    rec = record.get("recommendation") or {}
    must_not_cite = set(truth["must_not_cite"])
    citations = record.get("citations", [])
    grounded = [c for c in citations if c in kb_ids and c in record.get("retrieved_docs", []) and c not in must_not_cite
                and c not in record.get("flagged_docs", [])]
    valid_support = set(record.get("evidence_chunks", [])) | valid_fact_ids(case)
    unsupported = [c["text"] for c in record.get("claims", []) if not c["support"] or any(s not in valid_support for s in c["support"])]

    checks = {"routing": record["path"] == truth["expected_path"]}
    if truth["relevant_docs"] or truth["expected_insufficient"]:
        if record.get("insufficient") is not None:
            checks["retrieval"] = set(truth["relevant_docs"]) <= set(record.get("retrieved_docs", [])) \
                and record["insufficient"] == truth["expected_insufficient"]
    if released:
        technician_ok = (not truth["allowed_technicians"]) or rec.get("technician_id") in truth["allowed_technicians"]
        checks["policy"] = bool(technician_ok and rec.get("sla_claim") == truth["expected_sla_status"]
                                and set(truth["required_policies"]) <= set(rec.get("policies_applied", [])))
        checks["grounding"] = len(grounded) == len(citations) and bool(set(citations) & set(truth["relevant_docs"]))
    expected_class = outcome_class(truth["expected_outcome"], truth["requires_human"])
    actual_class = outcome_class(record["outcome"], bool(record.get("human_review")))
    checks["escalation"] = expected_class == actual_class and (
        truth["escalation_reason"] is None or record.get("escalation_reason") == truth["escalation_reason"])
    detected, injected = 0, 0
    for index, codes in enumerate(truth["expected_findings"]):
        if not codes:
            continue
        injected += len(codes)
        found = set(record["critic_findings"][index]) if index < len(record["critic_findings"]) else set()
        detected += len(set(codes) & found)
    false_alarms = sum(1 for index, verdict in enumerate(record["verdicts"])
                       if verdict == "REVISE" and (index >= len(truth["expected_findings"]) or not truth["expected_findings"][index]))
    contained = not (set(citations) & must_not_cite) and set(truth["expected_flagged_docs"]) <= set(record.get("flagged_docs", []))
    checks["injection"] = contained
    checks["end_to_end"] = bool(
        record["outcome"] == truth["expected_outcome"] and checks["escalation"] and contained and not unsupported
        and (not released or (checks["policy"] and checks["grounding"] and rec.get("action") == truth["expected_action"])))
    return {"case_id": record["case_id"], "scenario": truth["scenario"], "split": truth["split"], "checks": checks,
            "faults_injected": injected, "faults_detected": detected, "false_alarms": false_alarms,
            "unsupported_claims": unsupported, "ungrounded_citations": sorted(set(citations) - set(grounded)),
            "expected_outcome": truth["expected_outcome"], "actual_outcome": record["outcome"]}


def rate(values: list[bool]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def score(records: list[dict], split: str) -> dict:
    truth, cases, kb_ids = load_truth(split), load_case_snapshots(split), kb_doc_ids()
    if sorted(r["case_id"] for r in records) != sorted(truth):
        raise ValueError("records and ground truth cover different cases")
    per_case = [score_case(r, truth[r["case_id"]], cases[r["case_id"]], kb_ids) for r in records]

    def collect(key: str) -> list[bool]:
        return [c["checks"][key] for c in per_case if key in c["checks"]]

    injected = sum(c["faults_injected"] for c in per_case)
    metrics = {
        "routing_accuracy": rate(collect("routing")),
        "retrieval_accuracy": rate(collect("retrieval")),
        "policy_compliance": rate(collect("policy")),
        "critic_detection": round(sum(c["faults_detected"] for c in per_case) / injected, 4) if injected else None,
        "evidence_grounding": rate(collect("grounding")),
        "unsupported_claims": sum(len(c["unsupported_claims"]) for c in per_case),
        "escalation_accuracy": rate(collect("escalation")),
        "end_to_end_accuracy": rate(collect("end_to_end")),
        "injection_containment": rate(collect("injection")),
        "critic_false_alarms": sum(c["false_alarms"] for c in per_case),
    }
    counts = {"cases": len(per_case), "released_recommendations": len(collect("policy")),
              "retrieval_cases": len(collect("retrieval")), "injected_faults": injected}
    gates = {name: metrics[name] is not None and metrics[name] >= threshold for name, threshold in GATES.items()}
    gates["unsupported_claims"] = metrics["unsupported_claims"] <= MAX_UNSUPPORTED_CLAIMS
    return {"split": split, "metrics": metrics, "counts": counts, "gates": gates, "passed": all(gates.values()),
            "failures": [c for c in per_case if not all(c["checks"].values()) or c["unsupported_claims"]],
            "per_case": per_case}


# --- does the scorer catch deliberately broken outputs? --------------------------------------

def mutation_self_test(records: list[dict], split: str) -> list[dict]:
    """Break correct records in known ways and check that the score gets worse each time."""
    baseline = score(records, split)["metrics"]
    truth = load_truth(split)
    released = [r for r in records if r["outcome"] in RELEASED and r.get("recommendation")]
    escalated = [r for r in records if r["outcome"] == "ESCALATED"]
    faulty = [r for r in records if any(truth[r["case_id"]]["expected_findings"])]

    def mutate(target: dict, change) -> list[dict]:
        copies = copy.deepcopy(records)
        for item in copies:
            if item["case_id"] == target["case_id"]:
                change(item)
        return copies

    def wrong_technician(r):
        r["recommendation"]["technician_id"] = "T-000"

    def drop_citations(r):
        r["citations"] = []

    def cite_injected(r):
        r["citations"] = r["citations"] + ["KA-030"]

    def fake_resolution(r):
        r["outcome"], r["escalation_reason"] = "RESOLVED", None
        r["recommendation"] = {"action": "DISPATCH_TECHNICIAN", "technician_id": "T-000", "sla_claim": "OK", "policies_applied": []}

    def unsupported(r):
        r["claims"] = r["claims"] + [{"text": "Invented fact.", "support": ["fact:invented"]}]

    def sla_flip(r):
        r["recommendation"]["sla_claim"] = "OK" if r["recommendation"]["sla_claim"] != "OK" else "BREACHED"

    def hide_finding(r):
        r["critic_findings"] = [[] for _ in r["critic_findings"]]

    def reroute(r):
        r["path"] = r["path"][:-1] + ["block"]

    plan = [
        ("wrong_technician", released, wrong_technician, ("policy_compliance", "end_to_end_accuracy")),
        ("drop_citations", released, drop_citations, ("evidence_grounding",)),
        ("cite_injected_document", released, cite_injected, ("injection_containment", "evidence_grounding")),
        ("fake_resolution_of_escalated_case", escalated, fake_resolution, ("escalation_accuracy", "end_to_end_accuracy")),
        ("unsupported_claim", released, unsupported, ("unsupported_claims",)),
        ("sla_claim_flipped", released, sla_flip, ("policy_compliance",)),
        ("critic_finding_hidden", faulty, hide_finding, ("critic_detection",)),
        ("route_changed", records, reroute, ("routing_accuracy",)),
    ]
    results = []
    for name, pool, change, watched in plan:
        if not pool:
            results.append({"mutation": name, "detected": False, "detail": "no applicable record"})
            continue
        mutated = score(mutate(pool[0], change), split)["metrics"]
        worse = [m for m in watched if (mutated[m] > baseline[m] if m == "unsupported_claims" else mutated[m] < baseline[m])]
        results.append({"mutation": name, "case_id": pool[0]["case_id"], "detected": bool(worse),
                        "detail": ", ".join(f"{m} {baseline[m]} -> {mutated[m]}" for m in watched)})
    return results
