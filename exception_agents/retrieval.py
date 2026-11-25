"""Lexical retrieval (BM25) over the synthetic knowledge base. This is not vector search.

Articles are split into paragraph chunks (``KA-001#1``); the title is indexed with every
chunk. Scores use Okapi BM25 with the non-negative IDF variant. Ties are broken by chunk id,
so results are deterministic.

Retrieved text is untrusted data: chunks that contain instruction-like text are flagged and
kept out of the citable evidence.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

TOKEN = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    "a an and are as at be been but by for from has have if in into is it its may must no not of on "
    "or so than that the their then there these this to too was were when where which while with".split()
)

# Instruction-like patterns. Matching text is never followed; the chunk is excluded from evidence.
INJECTION_PATTERNS = (
    ("ignore_instructions", re.compile(r"\b(ignore|disregard|forget)\b[^.]{0,40}\b(instructions?|polic(y|ies)|rules?)\b", re.I)),
    ("role_marker", re.compile(r"(^|\.\s+|\n)\s*(system|assistant|developer)\s*(override|prompt|message)?\s*:", re.I)),
    ("directive_to_model", re.compile(r"\byou (must|should|are required to) (now )?(approve|assign|mark|ignore|skip)\b", re.I)),
    ("sla_override", re.compile(r"\bmark (the )?sla as (met|compliant)\b", re.I)),
    ("markup_instruction", re.compile(r"<\s*/?\s*(system|instructions?)\s*>", re.I)),
)


def tokenize(text: str) -> list[str]:
    tokens = []
    for token in TOKEN.findall(text.lower()):
        if token in STOPWORDS or len(token) < 2:
            continue
        if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]  # light plural folding: "valves" -> "valve"
        tokens.append(token)
    return tokens


def injection_flags(text: str) -> list[str]:
    return [name for name, pattern in INJECTION_PATTERNS if pattern.search(text)]


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    title: str
    category: str
    text: str
    equipment: tuple[str, ...]


def chunk_articles(articles: list[dict]) -> list[Chunk]:
    chunks = []
    for article in articles:
        for index, paragraph in enumerate(article["body"], start=1):
            chunks.append(Chunk(f"{article['doc_id']}#{index}", article["doc_id"], article["title"],
                                article["category"], paragraph, tuple(article.get("equipment", []))))
    return chunks


class BM25Index:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.docs = [tokenize(f"{c.title} {c.text}") for c in chunks]
        self.lengths = [len(d) for d in self.docs]
        self.avgdl = sum(self.lengths) / len(self.lengths) if self.lengths else 0.0
        self.tf = [Counter(d) for d in self.docs]
        df = Counter(term for doc in self.docs for term in set(doc))
        n = len(self.docs)
        self.idf = {term: math.log(1 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()}
        # A document with instruction-like text anywhere is untrusted as a whole, not just that paragraph.
        self.doc_flags: dict[str, list[str]] = {}
        for chunk in chunks:
            for flag in injection_flags(chunk.text):
                flags = self.doc_flags.setdefault(chunk.doc_id, [])
                if f"{flag} in {chunk.chunk_id}" not in flags:
                    flags.append(f"{flag} in {chunk.chunk_id}")

    def score(self, query_terms: list[str], index: int) -> float:
        tf, length = self.tf[index], self.lengths[index]
        total = 0.0
        for term in query_terms:
            if term not in tf:
                continue
            freq = tf[term]
            denominator = freq + self.k1 * (1 - self.b + self.b * length / self.avgdl)
            total += self.idf[term] * freq * (self.k1 + 1) / denominator
        return total

    def search(self, query: str, top_k: int) -> list[tuple[Chunk, float]]:
        terms = list(dict.fromkeys(tokenize(query)))  # de-duplicated, order kept
        scored = [(self.chunks[i], self.score(terms, i)) for i in range(len(self.chunks))]
        scored = [item for item in scored if item[1] > 0]
        scored.sort(key=lambda item: (-round(item[1], 9), item[0].chunk_id))
        return scored[:top_k]


def build_index(articles: list[dict]) -> BM25Index:
    return BM25Index(chunk_articles(articles))
