"""Synthetic dataset: reproducible, complete, fictional, split into dev and holdout."""

import json
import re
from collections import Counter
from pathlib import Path

from exception_agents import generator
from exception_agents.config import PACKAGE_DATA, knowledge_base, policies
from exception_agents.models import ExceptionCase

SCENARIOS = {name for name, *_ in generator.PLAN}


def test_generation_is_deterministic_and_matches_committed_files():
    first, second = generator.render(), generator.render()
    assert first == second
    for rel, text in first.items():
        committed = (Path(str(PACKAGE_DATA)) / rel).read_text(encoding="utf-8").replace("\r\n", "\n")
        assert committed == text, f"{rel} drifted; run 'python -m exception_agents generate'"


def test_split_sizes_and_scenario_coverage(cases):
    by_split = Counter(c.split for c in cases.values())
    assert by_split == {"dev": 20, "holdout": 37}
    assert 50 <= len(cases) <= 60
    for split in ("dev", "holdout"):
        assert {c.scenario for c in cases.values() if c.split == split} == SCENARIOS


def test_every_case_validates_and_has_ground_truth(cases, truth):
    assert set(cases) == set(truth)
    for case_id, case in cases.items():
        ExceptionCase.model_validate(case.model_dump())
        gt = truth[case_id]
        assert gt["scenario"] == case.scenario and gt["split"] == case.split
        assert gt["expected_path"][0] == "intake" and gt["expected_path"][-1] in ("respond", "escalate", "block")
        assert len(gt["expected_findings"]) == len(gt["injected_faults"])


def test_dev_and_holdout_are_different_cases_with_different_wording(cases):
    dev = {c.service_request.symptom for c in cases.values() if c.split == "dev"}
    holdout = {c.service_request.symptom for c in cases.values() if c.split == "holdout"}
    assert not (dev & holdout - {""}), "holdout reuses dev symptom wording"
    assert not ({c.service_request.request_id for c in cases.values() if c.split == "dev"}
                & {c.service_request.request_id for c in cases.values() if c.split == "holdout"})


def test_knowledge_base_size_ids_and_injection_documents():
    articles = knowledge_base()
    assert 20 <= len(articles) <= 40
    ids = [a["doc_id"] for a in articles]
    assert len(ids) == len(set(ids)) and all(re.fullmatch(r"KA-\d{3}", i) for i in ids)
    injected = [a["doc_id"] for a in articles if re.search(r"ignore all previous|disregard the policies", " ".join(a["body"]), re.I)]
    assert injected == ["KA-030", "KA-031"], "exactly the two documented test documents carry injected text"
    for policy in policies()["policies"].values():
        assert policy["doc_id"] in ids


def test_content_is_fictional_and_free_of_contacts_and_urls():
    text = json.dumps([knowledge_base(), policies()]) + "".join(
        (Path(str(PACKAGE_DATA)) / rel).read_text(encoding="utf-8") for rel in generator.render())
    assert "(fictional)" in text
    for pattern in (r"https?://", r"[\w.+-]+@[\w-]+\.[a-z]{2,}", r"\+?\d{3}[ -]\d{3}[ -]\d{4}"):
        assert not re.search(pattern, text), pattern


def test_every_failure_mode_maps_to_an_existing_article_or_is_deliberately_uncovered():
    ids = {a["doc_id"] for a in knowledge_base()}
    uncovered = [name for name, fm in generator.FAILURE_MODES.items() if fm.doc is None]
    assert uncovered == ["menu_board_controller", "conveyor_belt_jam"]
    for name, fm in generator.FAILURE_MODES.items():
        if fm.doc:
            article = next(a for a in knowledge_base() if a["doc_id"] == fm.doc)
            assert fm.doc in ids and fm.category in article["equipment"] and name in article["failure_modes"]
