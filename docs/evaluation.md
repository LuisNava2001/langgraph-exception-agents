# Evaluation

## Cases

57 synthetic cases from [`generator.py`](../exception_agents/generator.py), split by seed and wording.

| Scenario | dev | holdout | Designed outcome |
| --- | --- | --- | --- |
| correct_resolution | 3 | 3 | RESOLVED, dispatch |
| sla_breached | 1 | 3 | RESOLVED, SLA reported as BREACHED, breach policy applied |
| resource_unavailable (technician on leave, part delay, competing order) | 3 | 6 | RESOLVED: reassign, or wait for the part with mitigation |
| policy_violation (restart a gas appliance without inspection) | 1 | 2 | BLOCKED |
| policy_incompatible_draft (policy omitted, wrong skill) | 1 | 2 | REVISE then RESOLVED |
| insufficient_information (no priority, symptom or equipment) | 1 | 2 | ESCALATED at intake |
| contradictory_evidence | 1 | 2 | HUMAN_REVIEW, then approved or rejected |
| irrelevant_retrieval (equipment the knowledge base does not cover) | 1 | 2 | ESCALATED, insufficient evidence |
| human_escalation (cost approval, priority tie) | 2 | 3 | HUMAN_REVIEW, then approved or rejected |
| critic_revision (unavailable or unknown technician, part promise, SLA claim) | 2 | 3 | REVISE then RESOLVED |
| prompt_injection (documents and request text) | 1 | 3 | RESOLVED, injected documents quarantined |
| unsupported_response (missing document, unsupported claim, quarantined citation) | 1 | 2 | REVISE then RESOLVED |
| agent_error (research or analysis fails) | 1 | 2 | ESCALATED, agent error |
| revision_limit (the draft never improves) | 1 | 2 | ESCALATED after 2 revisions |

## Ground truth

Written by the generator from how each case was constructed, not from agent output: the expected node path and
critic verdicts, outcome, escalation reason, action, the technicians that are acceptable, the SLA status set up,
required policies, relevant diagnostic articles, whether evidence should be insufficient, documents that must never be
cited, documents that must be quarantined, injected faults per draft and the critic codes each should raise.

## Independent scorer

[`evaluator.py`](../exception_agents/evaluator.py) reads run records and the ground-truth files. It imports none of
the agent, critic, validation, rules or reasoner modules (a test parses its imports), and does not recompute
eligibility, SLA or policy requirements.

| Metric | Definition | Gate |
| --- | --- | --- |
| Routing accuracy | node path equals the designed path | ≥ 95% |
| Retrieval accuracy | relevant articles retrieved and the insufficiency flag correct (cases where research ran) | ≥ 90% |
| Policy compliance | released recommendations: technician acceptable, SLA status true, required policies applied | 100% |
| Critic detection | injected faults whose expected critic code was raised | ≥ 95% |
| Evidence grounding | released recommendations: every citation retrieved and trusted, at least one relevant | 100% |
| Unsupported claims | claims whose support is not a retrieved chunk or a fact in the case snapshot | 0 |
| Escalation accuracy | auto-resolve / human review / escalate / block class and reason match | ≥ 95% |
| End-to-end accuracy | outcome, action, compliance, grounding, containment and claims all correct | ≥ 95% |
| Injection containment | injected documents quarantined and never cited | 100% |

`python -m exception_agents eval` exits 4 when a gate fails.

## Results (Demo Mode, 2026-11-25)

| Metric | dev (20 cases) | holdout (37 cases) |
| --- | --- | --- |
| Routing accuracy | 1.0 | 1.0 |
| Retrieval accuracy | 1.0 (19 cases) | 1.0 (34 cases) |
| Policy compliance | 1.0 (14 released) | 1.0 (24 released) |
| Critic detection | 1.0 (7 faults) | 1.0 (13 faults) |
| Evidence grounding | 1.0 | 1.0 |
| Unsupported claims | 0 | 0 |
| Escalation accuracy | 1.0 | 1.0 |
| End-to-end accuracy | 1.0 | 1.0 |
| Injection containment | 1.0 | 1.0 |
| Critic false alarms | 0 | 0 |

**These are results of deterministic code on designed cases.** They show that the workflow does what it was built
to do and stays that way; they are not measurements of a language model, and not evidence of generalisation or
production performance. In Demo Mode the drafts come from a scripted reasoner whose mistakes are declared in the case
file.

## Negative controls (all 57 cases)

| Control | Gates | End-to-end | Policy compliance | Critic detection | Injection containment |
| --- | --- | --- | --- | --- | --- |
| `critic_off` | fail (routing, critic detection) | 1.0 | 1.0 | 0.0 | 1.0 |
| `validator_off` | pass | 1.0 | 1.0 | 1.0 | 1.0 |
| `critic_and_validator_off` | fail (7 gates) | 0.5088 | 0.7255 | 0.0 | 0.9825 |
| `retrieval_off` | fail (8 gates) | 0.2105 | n/a (nothing released) | 0.65 | 0.7895 |
| `injection_filter_off` (and a reasoner that obeys documents) | fail (5 gates) | 0.7719 | 1.0 | 0.95 | 0.7895 |

`validator_off` passing is a real result: on this dataset the critic catches every injected fault, so the gate is
redundant here.

## Scorer self-test

`eval --self-test` breaks correct records in eight ways (wrong technician, citations dropped, an injected document
cited, an escalated case turned into a fake resolution, an unsupported claim, a flipped SLA claim, a hidden critic
finding, a changed route). Each one lowers the metric it should. All eight are detected on both splits.

## Methodological limits

* The holdout uses a different seed and different symptom wording, but the same author wrote both, and the holdout
  was run during development: an early full run exposed that injection flags were applied per paragraph instead of
  per document, which affected both splits. It is a reserved set, not a blind one.
* Scenario proportions are designed, not observed; 100% means "no regression on these designs".
* No language-model evaluation was run (LLM Mode is NOT TESTED). LLM-as-judge metrics are not implemented.
