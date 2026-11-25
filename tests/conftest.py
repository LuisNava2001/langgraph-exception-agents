"""Shared fixtures. Everything runs offline in Demo Mode on the real LangGraph graph."""

from __future__ import annotations

import pytest

from exception_agents.agents import make_deps
from exception_agents.evaluator import load_truth
from exception_agents.harness import run_records
from exception_agents.runner import load_cases, run_simulated


@pytest.fixture(scope="session")
def deps():
    return make_deps()


@pytest.fixture(scope="session")
def cases():
    return {case.case_id: case for case in load_cases("all")}


@pytest.fixture(scope="session")
def truth():
    return load_truth("all")


@pytest.fixture(scope="session")
def runs(deps, cases):
    """Final state of every case, run once through the graph."""
    return {case_id: run_simulated(case, deps) for case_id, case in cases.items()}


@pytest.fixture(scope="session")
def records():
    return run_records("all", "none")[0]


def first_case(cases, scenario: str, predicate=lambda c: True):
    return next(c for c in cases.values() if c.scenario == scenario and predicate(c))
