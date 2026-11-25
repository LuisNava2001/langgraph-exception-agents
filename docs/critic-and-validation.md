# Critic and validation

Two reviewers look at every draft. The **critic** diagnoses problems, suggests fixes and picks a route. The
**validation gate** runs deterministic hard checks after the critic approves and after any human decision; nothing
reaches `respond` without passing it.

## Critic findings

| Code | Severity | Meaning |
| --- | --- | --- |
| `FORBIDDEN_ACTION` | block | the request asks for an action POL-FORBIDDEN-ACTIONS forbids |
| `INSUFFICIENT_EVIDENCE` | evidence | no diagnostic evidence was retrieved |
| `UNKNOWN_TECHNICIAN`, `SKILL_MISMATCH`, `CERTIFICATION_MISSING`, `REGION_MISMATCH`, `TECHNICIAN_UNAVAILABLE`, `TECHNICIAN_BOOKED` | fix | the assignment is impossible or against policy |
| `PART_NOT_AVAILABLE`, `UNREALISTIC_COMPLETION` | fix | the draft promises parts or a time the facts do not support |
| `SLA_MISREPRESENTED` | fix | SLA status differs from the computed one |
| `POLICY_OMITTED` | fix | a required policy or mitigation is missing |
| `CITATION_NOT_FOUND`, `CITATION_NOT_RETRIEVED`, `UNTRUSTED_SOURCE_CITED`, `MISSING_EVIDENCE_CITATION` | fix | citations do not match the retrieved, trusted evidence |
| `UNSUPPORTED_CLAIM`, `MISSING_FIELD` | fix | a claim without valid support, or an empty required field |
| `COST_APPROVAL_REQUIRED`, `CONTRADICTION`, `PRIORITY_TIE`, `NO_FEASIBLE_ASSIGNMENT` | human | a person must decide |

Verdict: any block → `POLICY_VIOLATION`; else any evidence → `INSUFFICIENT_EVIDENCE`; else any fix → `REVISE`; else
any human → `HUMAN_REVIEW`; else `APPROVED`. Human findings are suppressed once a reviewer approved *the same
draft*; a revised draft needs a new approval.

## Validation gate

| Check | Route on failure |
| --- | --- |
| `V01_NO_FORBIDDEN_ACTION` | block |
| `V02_EVIDENCE_AVAILABLE` | escalate |
| `V03_TECHNICIAN_ELIGIBLE` (technician in the computed eligible list) | revise |
| `V04_PARTS_PROMISE` | revise |
| `V05_SLA_TRUTHFUL` | revise |
| `V06_POLICIES_APPLIED` | revise |
| `V07_CITATIONS_VALID` (all cited documents retrieved and trusted, at least one diagnostic) | revise |
| `V08_CLAIMS_SUPPORTED` | revise |
| `V09_REQUIRED_FIELDS` | revise |
| `V10_HUMAN_APPROVAL` (when a human trigger exists) | human_review |

A human approval satisfies only `V10`. It cannot waive a forbidden action, an ineligible technician or an untrue SLA
claim; tests check exactly that.

## Defense in depth, measured

| Configuration | What happens to faulty drafts |
| --- | --- |
| normal | the critic flags every injected fault (20/20); the gate passes the revised drafts |
| critic off | the critic approves everything; the gate rejects each faulty draft and sends it back for revision; every released recommendation is still compliant (policy compliance 1.0, end-to-end 1.0) |
| gate off | no measurable change on this dataset, because the critic already catches every injected fault |
| both off | faulty drafts reach the response: policy compliance 0.7255, end-to-end 0.5088 |

The gate earns its place when the critic is wrong, for example when a language model takes over drafting or
reviewing. On this deterministic dataset it is redundant, and the evaluation says so.

## Where the facts come from

Both reviewers check drafts against [`rules.py`](../exception_agents/rules.py): SLA deadline and status, technician
eligibility (skill, hazard certification, region, leave, shift window, competing orders by priority and deadline),
part stock and arrival, required policies and mitigation, contradictions between reported facts, cost threshold and
forbidden requested actions. A model can propose; it cannot change these facts.
