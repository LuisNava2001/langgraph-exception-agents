"""CLI exit codes and the pause/resume flow in separate processes; demo data; security properties."""

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import exception_agents
from exception_agents.config import EXIT_EVALUATION, EXIT_INPUT, EXIT_OK, EXIT_USAGE

REPO = Path(__file__).resolve().parents[1]
PACKAGE = Path(exception_agents.__file__).parent


def cli(*args, env=None):
    return subprocess.run([sys.executable, "-m", "exception_agents", *args], capture_output=True, text=True, cwd=REPO,
                          env=env, timeout=300)


def test_help_and_usage_errors():
    result = cli("--help")
    assert result.returncode == EXIT_OK and "exit codes" in result.stdout
    for command in ("cases", "run", "review", "show", "eval", "demo", "generate"):
        assert command in result.stdout
    assert cli().returncode == EXIT_USAGE
    assert cli("review", "thread-x", "--decision", "maybe", "--reviewer", "r").returncode == EXIT_USAGE


def test_unknown_case_and_thread_exit_3(tmp_path):
    db = ["--db", str(tmp_path / "c.sqlite"), "--review-db", str(tmp_path / "r.sqlite")]
    assert cli("run", "EXC-NOPE-1", *db).returncode == EXIT_INPUT
    assert cli("show", "nope", *db).returncode == EXIT_INPUT


def test_pause_resume_in_separate_processes(tmp_path):
    db = ["--db", str(tmp_path / "c.sqlite"), "--review-db", str(tmp_path / "r.sqlite")]
    paused = cli("run", "EXC-DEV-014", *db)
    assert paused.returncode == EXIT_OK and "AWAITING_REVIEW" in paused.stdout
    assert "AWAITING_REVIEW" in cli("show", "EXC-DEV-014", *db).stdout
    resumed = cli("review", "EXC-DEV-014", "--decision", "approve", "--reviewer", "dispatcher-a", "--note", "ok", *db)
    assert resumed.returncode == EXIT_OK and "RESOLVED_AFTER_REVIEW" in resumed.stdout
    again = cli("review", "EXC-DEV-014", "--decision", "reject", "--reviewer", "dispatcher-b", *db)
    assert again.returncode == EXIT_OK and "nothing changed" in again.stdout and "approve by dispatcher-a" in again.stdout
    shown = cli("show", "EXC-DEV-014", *db).stdout
    assert shown.count("review log:") == 1 and "not authenticated" in shown
    assert cli("run", "EXC-DEV-014", *db).returncode == EXIT_INPUT


def test_eval_gates_and_negative_control_exit_codes():
    assert cli("eval", "--split", "dev", "--self-test").returncode == EXIT_OK
    assert cli("eval", "--control", "retrieval_off").returncode == EXIT_EVALUATION


def test_committed_files_are_reproducible():
    assert cli("generate", "--check").returncode == EXIT_OK
    assert cli("demo", "--check").returncode == EXIT_OK


def test_demo_data_comes_from_real_runs_and_labels_simulation():
    text = (REPO / "demo" / "data.js").read_text(encoding="utf-8")
    data = json.loads(text[len("window.AGENT_DEMO = "):-2])
    assert len(data["cases"]) == 57 and "no language model" in data["mode"]
    reviewed = [c for c in data["cases"] if c["human_review"]]
    assert reviewed and all(c["human_review"]["simulated"] for c in reviewed)
    assert {c["control"] for c in data["controls"]} >= {"critic_off", "retrieval_off", "injection_filter_off"}
    assert all(m["detected"] for m in data["mutation_self_test"])
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", text), "no wall-clock timestamps"


def test_demo_page_is_offline():
    for name in ("index.html", "app.js", "styles.css"):
        content = (REPO / "demo" / name).read_text(encoding="utf-8")
        assert "http://" not in content and "https://" not in content and "fetch(" not in content, name


def _calls(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            names.add(func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else "")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {a.name for a in node.names} | ({node.module} if isinstance(node, ast.ImportFrom) and node.module else set())
    return names


def test_agents_cannot_run_commands_or_reach_the_network():
    forbidden = {"system", "popen", "Popen", "run", "eval", "exec", "subprocess", "socket", "requests", "urllib", "httpx"}
    for module in ("agents", "critic", "validation", "rules", "response", "graph", "retrieval", "reasoners"):
        found = _calls(PACKAGE / f"{module}.py") & forbidden
        assert not found, (module, found)


def test_agent_modules_do_not_write_files():
    for module in ("agents", "critic", "validation", "rules", "response", "graph", "retrieval", "reasoners"):
        assert not _calls(PACKAGE / f"{module}.py") & {"open", "write_text", "write_bytes", "unlink", "rmtree"}, module


def test_api_keys_never_reach_outputs(tmp_path):
    secret = "sk-test-NEVER-PRINT-THIS-1234"
    env = dict(os.environ, ANTHROPIC_API_KEY=secret)
    outputs = [cli("eval", "--split", "dev", env=env), cli("run", "EXC-DEV-001", "--db", str(tmp_path / "c.sqlite"),
                                                         "--review-db", str(tmp_path / "r.sqlite"), env=env)]
    for result in outputs:
        assert secret not in result.stdout + result.stderr
    with open(tmp_path / "c.sqlite", "rb") as handle:
        assert secret.encode() not in handle.read()
