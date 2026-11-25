# Human in the loop

## Mechanism

The `human_review` node calls LangGraph's `interrupt(payload)`. The graph stops, the checkpointer keeps the state,
and `invoke` returns with `__interrupt__`. Resuming is `graph.invoke(Command(resume=decision), config)` on the same
`thread_id`; the decision becomes the return value of `interrupt()`.

Payload sent to the reviewer:

```json
{"case_id": "EXC-DEV-014", "request_id": "SR-5014", "question": "Approve this recommendation?",
 "allowed_decisions": ["approve", "reject"], "triggers": ["COST_APPROVAL_REQUIRED"],
 "draft": {"action": "DISPATCH_TECHNICIAN", "technician_id": "T-241", "sla_claim": "OK", "estimated_cost": 3700.0, "...": "..."}}
```

The decision is validated (`approve` or `reject`, a reviewer label, an optional note). An invalid decision is an
agent error and the case escalates.

| Decision | Path |
| --- | --- |
| approve | `human_review → validate → respond` (outcome `RESOLVED_AFTER_REVIEW`), unless the gate finds a hard violation |
| reject | `human_review → escalate` (outcome `REJECTED`, reason `HUMAN_REJECTED`) |

Triggers: estimated cost above 2,500 (`COST_APPROVAL_REQUIRED`), contradictory field reports (`CONTRADICTION`), a
competing order with the same priority and deadline (`PRIORITY_TIE`), or no eligible technician.

## Persistence across processes

The CLI uses `SqliteSaver` (`langgraph-checkpoint-sqlite`) on `.runtime/checkpoints.sqlite` and runs with
`durability="sync"`, so the checkpoint is on disk before the command exits.

```bash
python -m exception_agents run EXC-DEV-014
#   ... 4. critic  ok  HUMAN_REVIEW: COST_APPROVAL_REQUIRED
#   AWAITING_REVIEW thread=EXC-DEV-014 triggers=['COST_APPROVAL_REQUIRED']
python -m exception_agents show EXC-DEV-014        # state, trace, pending review
python -m exception_agents review EXC-DEV-014 --decision approve --reviewer dispatcher-ana --note "budget ok"
#   ... 5. human_review ok approve by dispatcher-ana; 6. validate ok; 7. respond ok RESOLVED_AFTER_REVIEW
python -m exception_agents review EXC-DEV-014 --decision reject --reviewer someone-else
#   no pending review on EXC-DEV-014; nothing changed
#     recorded: approve by dispatcher-ana at <UTC time>
```

`tests/test_human_in_the_loop.py` closes the SQLite connection after the pause, opens a new one with a newly built
graph, and resumes, which is the same thing the CLI does across processes. `tests/test_cli_demo_security.py` runs the
commands above as separate processes.

## Re-execution and idempotency

* LangGraph restarts an interrupted node from its first line on resume. The review node therefore does nothing
  before `interrupt()`; a test counts two executions of the node for one review.
* Resuming a thread with no pending interrupt raises `NothingToResume` in the runner; the CLI prints the recorded
  decision and exits 0 without changing anything. A test checks the state is identical after the second attempt.
* The CLI writes each decision to `.runtime/reviews.sqlite` after a successful resume, keyed by thread and interrupt
  id, so a decision is logged once.
* A thread id cannot be started twice (exit code 3).

## Identities

Reviewer names are free-text labels. They are **not** authenticated, authorized or signed. A deployment would put
this behind real identity and access control; this project only shows the control flow.

## Simulated reviewers

Evaluation and demo runs answer interrupts automatically from each case's `simulation.reviewer` block. Those
decisions are stored with `simulated: true` and the demo labels them "simulated reviewer". No external effect is
triggered by any decision: the output is a recommendation text and state, nothing is dispatched.
