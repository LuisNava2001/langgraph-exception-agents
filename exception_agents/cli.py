"""Command-line interface.

Exit codes: 0 ok, 1 internal error, 2 usage, 3 input (unknown case or thread, thread exists),
4 evaluation gate failed or generated files drifted, 5 LLM Mode not configured.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from . import __version__
from .config import (
    DEFAULT_CHECKPOINT_DB,
    DEFAULT_REVIEW_DB,
    EXIT_EVALUATION,
    EXIT_INTERNAL,
    EXIT_OK,
    PACKAGE_DATA,
    AgentsError,
    InputError,
)

EPILOG = """exit codes:
  0  success (including a run paused for human review, and a repeated review that was a no-op)
  1  unexpected internal error
  2  usage error
  3  input error: unknown case or thread, or the thread id is already used
  4  evaluation gates failed, or generated files differ from the committed ones
  5  LLM Mode requested but the optional SDK or ANTHROPIC_API_KEY is missing"""


def _deps(mode: str):
    from .agents import make_deps

    if mode == "llm":
        from .reasoners import AnthropicReasoner

        return make_deps(reasoner=AnthropicReasoner())
    return make_deps()


def _print_trace(values: dict) -> None:
    for step in values.get("trace", []):
        print(f"  {step['step']:>2}. {step['node']:<13} {step['status']:<5} {step['summary']}")


def _print_pending(result) -> None:
    payload = result.interrupt["value"]
    print(f"AWAITING_REVIEW thread={result.thread_id} triggers={payload['triggers']}")
    print(f"  draft: {json.dumps(payload['draft'])}")
    print(f"  resume with: python -m exception_agents review {result.thread_id} --decision approve|reject --reviewer NAME")


def cmd_cases(args) -> int:
    from .runner import load_cases

    for case in load_cases(args.split):
        print(f"{case.case_id}  {case.split:<7} {case.scenario:<26} {case.exception.type}")
    return EXIT_OK


def cmd_run(args) -> int:
    from .graph import build_graph
    from .runner import get_case, open_checkpointer, start

    case = get_case(args.case_id)
    deps = _deps(args.mode)
    saver = open_checkpointer(Path(args.db))
    try:
        graph = build_graph(deps, saver)
        result = start(graph, case, args.thread or case.case_id, deps)
    finally:
        saver.conn.close()
    _print_trace(result.values)
    if result.status == "AWAITING_REVIEW":
        _print_pending(result)
    else:
        print(result.values["final"]["explanation"])
    return EXIT_OK


def cmd_review(args) -> int:
    from .graph import build_graph
    from .runner import NothingToResume, ReviewLog, config_for, open_checkpointer, pending_interrupt, resume

    deps = _deps(args.mode)
    saver = open_checkpointer(Path(args.db))
    log = ReviewLog(Path(args.review_db))
    try:
        graph = build_graph(deps, saver)
        waiting = pending_interrupt(graph, config_for(args.thread_id, deps))
        decision = {"decision": args.decision, "reviewer_id": args.reviewer, "note": args.note or "", "simulated": False}
        try:
            result = resume(graph, args.thread_id, decision, deps)
        except NothingToResume:
            print(f"no pending review on {args.thread_id}; nothing changed")
            for entry in log.entries(args.thread_id):
                print(f"  recorded: {entry['decision']} by {entry['reviewer_id']} at {entry['recorded_at']}")
            return EXIT_OK
        case_id = result.values["case"]["case_id"]
        log.record(args.thread_id, waiting["id"], case_id, decision)
    finally:
        saver.conn.close()
        log.close()
    _print_trace(result.values)
    if result.status == "AWAITING_REVIEW":
        _print_pending(result)
    else:
        print(result.values["final"]["explanation"])
    return EXIT_OK


def cmd_show(args) -> int:
    from .agents import make_deps
    from .graph import build_graph
    from .runner import ReviewLog, config_for, open_checkpointer, pending_interrupt

    deps = make_deps()
    saver = open_checkpointer(Path(args.db))
    try:
        graph = build_graph(deps, saver)
        config = config_for(args.thread_id, deps)
        values = graph.get_state(config).values
        if not values:
            raise InputError(f"unknown thread {args.thread_id!r}")
        waiting = pending_interrupt(graph, config)
        history = len(list(graph.get_state_history(config)))
    finally:
        saver.conn.close()
    print(f"thread {args.thread_id}: case {values['case']['case_id']}, {history} checkpoints")
    _print_trace(values)
    if waiting:
        print(f"AWAITING_REVIEW triggers={waiting['value']['triggers']}")
    elif values.get("final"):
        print(values["final"]["explanation"])
    log = ReviewLog(Path(args.review_db))
    for entry in log.entries(args.thread_id):
        print(f"review log: {entry['decision']} by {entry['reviewer_id']} at {entry['recorded_at']} (label, not authenticated)")
    log.close()
    return EXIT_OK


def cmd_eval(args) -> int:
    from .harness import evaluate

    report = evaluate(args.split, args.control, self_test=args.self_test)
    print(f"evaluation split={args.split} control={args.control} cases={report['counts']['cases']} "
          f"(released recommendations {report['counts']['released_recommendations']}, injected faults "
          f"{report['counts']['injected_faults']})")
    for name, value in report["metrics"].items():
        gate = report["gates"].get(name)
        mark = "" if gate is None else ("  pass" if gate else "  FAIL")
        print(f"  {name:<24} {value}{mark}")
    for failure in report["failures"][:10]:
        failed = [k for k, ok in failure["checks"].items() if not ok]
        print(f"  case {failure['case_id']} ({failure['scenario']}): failed {failed}")
    for item in report.get("mutation_self_test", []):
        print(f"  self-test {item['mutation']:<36} {'detected' if item['detected'] else 'MISSED'}  {item['detail']}")
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    ok = report["passed"] and all(i["detected"] for i in report.get("mutation_self_test", []))
    print("gates passed" if ok else "gates FAILED")
    return EXIT_OK if ok else EXIT_EVALUATION


def cmd_demo(args) -> int:
    from .demo import write_or_check

    ok, message = write_or_check(Path(args.out), args.check)
    print(message)
    return EXIT_OK if ok else EXIT_EVALUATION


def cmd_generate(args) -> int:
    from . import generator

    root = Path(str(PACKAGE_DATA))
    if args.check:
        drift = [rel for rel, text in generator.render().items()
                 if not (root / rel).exists() or (root / rel).read_text(encoding="utf-8").replace("\r\n", "\n") != text]
        print("generated cases match the committed files" if not drift else f"drift in {drift}")
        return EXIT_OK if not drift else EXIT_EVALUATION
    for rel in generator.write(root):
        print(f"wrote {rel}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m exception_agents",
                                     description="LangGraph multi-agent exception handling (synthetic data, Demo Mode by default).",
                                     epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    def add(name, handler, text):
        p = sub.add_parser(name, help=text, description=text, epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
        p.set_defaults(handler=handler)
        return p

    def storage(p):
        p.add_argument("--db", default=str(DEFAULT_CHECKPOINT_DB), help="SQLite checkpoint file")
        p.add_argument("--review-db", default=str(DEFAULT_REVIEW_DB), help="SQLite review log file")

    p = add("cases", cmd_cases, "List the synthetic exception cases.")
    p.add_argument("--split", choices=["dev", "holdout", "all"], default="all")
    p = add("run", cmd_run, "Run one case; stops if a human review is required.")
    p.add_argument("case_id")
    p.add_argument("--thread", help="thread id for the checkpointer (default: the case id)")
    p.add_argument("--mode", choices=["demo", "llm"], default="demo")
    storage(p)
    p = add("review", cmd_review, "Record a human decision and resume a paused thread.")
    p.add_argument("thread_id")
    p.add_argument("--decision", choices=["approve", "reject"], required=True)
    p.add_argument("--reviewer", required=True, help="reviewer label (not an authenticated identity)")
    p.add_argument("--note", default="")
    p.add_argument("--mode", choices=["demo", "llm"], default="demo")
    storage(p)
    p = add("show", cmd_show, "Show a thread's state, trace and review log.")
    p.add_argument("thread_id")
    storage(p)
    p = add("eval", cmd_eval, "Run the evaluation harness (exit 4 when a gate fails).")
    p.add_argument("--split", choices=["dev", "holdout", "all"], default="all")
    from .agents import CONTROLS
    p.add_argument("--control", choices=sorted(CONTROLS), default="none", help="negative control (default: none)")
    p.add_argument("--self-test", action="store_true", help="also check that the scorer catches deliberate mutations")
    p.add_argument("--json", help="write the full report to this file")
    p = add("demo", cmd_demo, "Build demo/data.js from real runs (or --check it).")
    p.add_argument("--out", default="demo/data.js")
    p.add_argument("--check", action="store_true")
    p = add("generate", cmd_generate, "Regenerate the synthetic cases (or --check them).")
    p.add_argument("--check", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        return args.handler(args)
    except AgentsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return EXIT_INTERNAL
