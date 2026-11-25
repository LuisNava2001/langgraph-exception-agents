"""Paths, settings, exit codes and error types."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path

PACKAGE_DATA = resources.files("exception_agents") / "data"

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_EVALUATION = 4
EXIT_LLM_CONFIG = 5

DEFAULT_RUNTIME_DIR = Path(".runtime")
DEFAULT_CHECKPOINT_DB = DEFAULT_RUNTIME_DIR / "checkpoints.sqlite"
DEFAULT_REVIEW_DB = DEFAULT_RUNTIME_DIR / "reviews.sqlite"


class AgentsError(Exception):
    exit_code = EXIT_INTERNAL


class InputError(AgentsError):
    exit_code = EXIT_INPUT


class EvaluationFailed(AgentsError):
    exit_code = EXIT_EVALUATION


class LLMConfigError(AgentsError):
    exit_code = EXIT_LLM_CONFIG


def read_json(name: str):
    return json.loads((PACKAGE_DATA / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def policies() -> dict:
    return read_json("policies.json")


@lru_cache(maxsize=1)
def knowledge_base() -> list[dict]:
    return read_json("knowledge_base.json")["articles"]


@dataclass(frozen=True)
class Settings:
    max_revisions: int
    top_k: int
    min_score: float
    cost_approval_threshold: float
    recursion_limit: int

    @classmethod
    def load(cls) -> "Settings":
        p = policies()
        return cls(
            max_revisions=int(p["review"]["max_revisions"]),
            top_k=int(p["retrieval"]["top_k"]),
            min_score=float(p["retrieval"]["min_score"]),
            cost_approval_threshold=float(p["policies"]["POL-COST-APPROVAL"]["threshold"]),
            recursion_limit=int(p["review"]["recursion_limit"]),
        )
