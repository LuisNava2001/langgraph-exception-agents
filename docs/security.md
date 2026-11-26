# Security and content

## Guardrails

| Risk | Control | Verified by |
| --- | --- | --- |
| Retrieved text tries to give orders | documents with instruction-like text are quarantined as a whole, never cited, left out of the LLM prompt | `test_retrieval.py`, `test_end_to_end.py::test_injected_instructions_in_documents_change_nothing` |
| Request text tries to give orders | flagged at intake and treated as data | `test_instruction_like_text_in_the_request_is_data` |
| A model obeys injected text anyway | the critic and gate reject unknown technicians and false SLA claims; the case escalates | `test_a_naive_model_that_obeys_documents_is_contained` |
| An agent overwrites a decision | per-node field ownership with schema validation | `test_a_rogue_agent_cannot_set_the_outcome` |
| A model or reviewer waives a hard rule | the validation gate is final; a human approval satisfies only the approval check | `test_forbidden_action_blocks_whatever_the_draft_says`, `test_contradictions_and_cost_need_a_human...` |
| Endless revision | revision budget plus LangGraph recursion limit | `test_revision_budget...`, `test_recursion_limit_is_a_hard_backstop` |
| Agents run commands or reach the network | agent modules import no subprocess, socket or HTTP library and call no `eval`/`exec` | `test_agents_cannot_run_commands_or_reach_the_network` |
| Recommendations cause external effects | the output is text and state; nothing is dispatched or written by agents | `test_agent_modules_do_not_write_files` |
| API keys leak into logs or checkpoints | keys are read only by the optional SDK; a test runs with a fake key set and searches outputs and the SQLite file | `test_api_keys_never_reach_outputs` |

## Content

* All data is synthetic: the company, customers, venues, technicians, equipment, documents and policies are
  invented. Names are composed from generic word lists.
* The documents, policies and prompts were written for this repository. No external system, endpoint or credential
  is used.
* KA-030 and KA-031 contain injected instructions on purpose; they are labelled as test documents in the knowledge
  base file.

## Limitations

* Injection detection is pattern-based and can miss new phrasings. Containment relies on the structure (facts and
  validation decide), not on detection alone.
* Reviewer identities are labels, not authentication.
* The SQLite files under `.runtime/` are local and unencrypted.
* LLM Mode would send case facts and trusted evidence to an external API; it is off by default and has not been run.
