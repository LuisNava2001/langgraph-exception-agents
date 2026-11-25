# Knowledge retrieval

**This is lexical retrieval (Okapi BM25), not vector search.** There are no embeddings and no vector store.

## Why not ChromaDB

ChromaDB 1.5.9 is compatible with Python 3.11, but its default embedding function downloads an ONNX model at first
use and pulls `onnxruntime` and a server stack. That breaks the "offline, no download" Demo Mode and makes CI depend
on a model download. BM25 in about 80 lines of Python is deterministic, inspectable and enough for 32 short articles.
A vector index is on the roadmap.

## Knowledge base

[`knowledge_base.json`](../exception_agents/data/knowledge_base.json): 32 synthetic articles for the fictional
Quellmark Field Service.

| Category | Articles |
| --- | --- |
| diagnostic | 19 (one per covered failure mode) |
| policy, procedure, protocol, escalation | 11 (SLA, breach handling, assignment and certification, competing orders, parts, food safety, gas, electrical, cost approval, contradictions, communication) |
| vendor bulletin, field notes | 2 (KA-030, KA-031), deliberately containing instruction-like text |

## Index and query

* Each article is split into paragraph chunks (`KA-001#2`); the title is indexed with every chunk.
* Tokens: lower case, alphanumeric, a short stop-word list, light plural folding (`valves` → `valve`).
* BM25 with k1 = 1.5, b = 0.75 and the non-negative IDF `ln(1 + (N - df + 0.5) / (df + 0.5))`.
* Ties are broken by chunk id, so rankings are deterministic.
* The research agent queries with the equipment class, the symptom, keywords for the exception type, hazard terms
  and "service level agreement resolution target", and keeps the top 6 chunks.

## What counts as evidence

| Hit | Evidence? |
| --- | --- |
| score below 4.0 | no |
| from a document with instruction-like text anywhere | no, quarantined (shown as flagged) |
| diagnostic article for another equipment class | no (off topic) |
| otherwise | yes: citable, and its chunk id can support a claim |

`insufficient` is true when no diagnostic article for the case's equipment class is evidence. The critic then
returns `INSUFFICIENT_EVIDENCE` and the case is escalated rather than answered.

## Untrusted content

Retrieved text is data. A document whose text matches any instruction pattern (`ignore … instructions`,
`SYSTEM:` style role markers, "you must approve …", "mark the SLA as met", `<system>` tags) is quarantined as a
whole, not just the matching paragraph: in KA-030 the first paragraph is ordinary advice and the second carries the
injection. Quarantined documents never become evidence, cannot be cited (`UNTRUSTED_SOURCE_CITED`) and are left out
of the LLM prompt. The same patterns flag instruction-like text inside the request itself; it is recorded and
ignored.

## Measured on the synthetic cases

The scorer checks that every diagnostic article labelled relevant for a case is among the retrieved documents, and
that the insufficiency flag matches the label: 19/19 dev and 34/34 holdout cases where research ran (retrieval
accuracy 1.0). A separate test checks that every covered failure mode's article is retrieved for both its dev and
its holdout wording.

## Limitations

* Lexical matching misses paraphrases that share no terms with the article.
* Thresholds (score 4.0, top 6) were set on the dev wording; holdout wording is different but written by the same
  author, so it is not an independent test of language variation.
* Pattern-based injection detection can be evaded by wording it does not anticipate; the defense in depth is that
  nothing in a retrieved document can change routing, facts or validation.
