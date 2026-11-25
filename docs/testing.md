# Testing

```bash
python -m pip install -e ".[dev]" -c constraints.txt
python -m pytest -v
python -m exception_agents eval --self-test        # evaluation gates and scorer self-test (exit 4 on failure)
python -m exception_agents demo --check           # demo/data.js equals a fresh run
python -m exception_agents generate --check       # committed cases equal a fresh generation
```

Everything runs offline on the real LangGraph graph in Demo Mode. No test calls a language model.

## Inventory

| File | Tests | Covers |
| --- | --- | --- |
| `test_graph.py` | 24 | StateGraph nodes and edges, state validation, field ownership (including a rogue node and the unguarded counter-example), every routing rule, revision budget, recursion limit |
| `test_retrieval.py` | 31 | BM25 against a hand computation, deterministic ranking, every covered failure mode retrieved in both wordings, injection patterns, document-level quarantine, research results on all cases, off-topic articles |
| `test_critic_validation.py` | 16 | each injected fault caught by the critic and by the gate, part promises, quarantined citations, forbidden actions, missing evidence, contradictions and cost, approval scope, the gate catching what a disabled critic misses |
| `test_end_to_end.py` | 11 | all 57 cases on their designed path and outcome, released recommendations against the ground truth, grounding of every citation and claim, refusals, agent errors, injected text in documents and requests, a naive model contained, reproducibility, no false LLM claims |
| `test_human_in_the_loop.py` | 9 | interrupt payload, approve and reject, SQLite persistence across a new connection, idempotent resume, node re-execution, invalid decisions, thread reuse, review log |
| `test_evaluation.py` | 14 | reproducible records, gates on dev, holdout and all, four negative controls failing, defense in depth, the gate's redundancy reported, mutation self-test, scorer independence |
| `test_llm_mode.py` | 6 | Demo Mode default, missing SDK or key, SDK absent, prompt safety, a fake model's drafts going through critic and gate, unparseable replies |
| `test_data.py` | 7 | generator determinism and drift, split sizes and coverage, schema and ground truth, distinct wording, knowledge base, fictional content |
| `test_cli_demo_security.py` | 10 | exit codes, pause and resume in separate processes, eval and controls through the CLI, reproducible files, demo data and offline page, no commands, network or file writes in agents, keys never in outputs |

## Local results (2026-11-25, Windows 11, Python 3.11.9)

| Run | Result |
| --- | --- |
| `python -m pytest -v` | 128 passed, 0 skipped, 0 failed |
| `eval --self-test` | all gates passed, 8/8 mutations detected |
| `demo --check`, `generate --check` | up to date |

## Continuous integration

`.github/workflows/ci.yml` runs the suite, the evaluation with the self-test, a negative control that must fail, and
the drift checks on Ubuntu and Windows with Python 3.11, 3.12 and 3.13, installing with `constraints.txt`. Results are
in the repository's Actions tab once the workflow has run.
