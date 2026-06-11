"""Local-first BM25 ranking over Odoo records.

Powers the knowledge tools: an agent indexes a bounded slice of records
once, then runs relevance-ranked free-text queries against it without
another RPC round-trip per question. Everything stays in process memory —
no embeddings service, no data leaving the machine, no new dependencies.

Indexes are per ``instance:model`` and bounded the same way the schema
cache is (entry cap via ``ODOO_MCP_KNOWLEDGE_MAX_DOCS``).
"""

from __future__ import annotations

import math
import os
import re
import threading
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

DEFAULT_KNOWLEDGE_MAX_DOCS = 5000
BM25_K1 = 1.5
BM25_B = 0.75

_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)
_TAG_RE = re.compile(r"<[^>]+>")


def knowledge_max_docs() -> int:
    raw = os.environ.get("ODOO_MCP_KNOWLEDGE_MAX_DOCS", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_KNOWLEDGE_MAX_DOCS
    except ValueError:
        value = DEFAULT_KNOWLEDGE_MAX_DOCS
    return max(1, value)


def tokenize(text: str) -> List[str]:
    """Lowercase, accent-fold, and split on word boundaries.

    Accent folding keeps Vietnamese/European text searchable with or
    without diacritics ("hóa đơn" matches "hoa don").
    """
    normalized = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return _TOKEN_RE.findall(stripped)


def flatten_record_text(record: Dict[str, Any]) -> str:
    """Concatenate the searchable text of one record's field values."""
    parts: List[str] = []
    for key, value in record.items():
        if key == "id":
            continue
        if isinstance(value, str):
            # Strip markup from HTML fields so tags don't pollute the
            # token space or the returned snippets.
            if "<" in value and ">" in value:
                value = _TAG_RE.sub(" ", value)
            parts.append(value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            parts.append(str(value))
        elif isinstance(value, (list, tuple)):
            # Odoo many2one tuples are (id, display_name); keep the name.
            parts.extend(str(item) for item in value if isinstance(item, str))
    return " ".join(parts)


@dataclass
class IndexedDocument:
    record_id: int
    text: str
    tokens: Counter[str] = field(default_factory=Counter)
    length: int = 0


class BM25Index:
    """One BM25 corpus for a single ``instance:model`` pair."""

    def __init__(self) -> None:
        self.documents: Dict[int, IndexedDocument] = {}
        self.document_frequency: Counter[str] = Counter()
        self.total_length = 0

    def add(self, record_id: int, text: str) -> None:
        self.remove(record_id)
        tokens = Counter(tokenize(text))
        doc = IndexedDocument(
            record_id=record_id,
            text=text,
            tokens=tokens,
            length=sum(tokens.values()),
        )
        self.documents[record_id] = doc
        self.total_length += doc.length
        for term in tokens:
            self.document_frequency[term] += 1

    def remove(self, record_id: int) -> None:
        doc = self.documents.pop(record_id, None)
        if doc is None:
            return
        self.total_length -= doc.length
        for term in doc.tokens:
            self.document_frequency[term] -= 1
            if self.document_frequency[term] <= 0:
                del self.document_frequency[term]

    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        terms = tokenize(query)
        if not terms or not self.documents:
            return []
        doc_count = len(self.documents)
        avg_length = self.total_length / doc_count if doc_count else 0.0
        scores: Dict[int, float] = {}
        for term in terms:
            term_df = self.document_frequency.get(term, 0)
            if term_df == 0:
                continue
            idf = math.log(1 + (doc_count - term_df + 0.5) / (term_df + 0.5))
            for record_id, doc in self.documents.items():
                tf = doc.tokens.get(term, 0)
                if tf == 0:
                    continue
                denom = tf + BM25_K1 * (
                    1 - BM25_B + BM25_B * (doc.length / avg_length if avg_length else 1)
                )
                scores[record_id] = scores.get(record_id, 0.0) + idf * (
                    tf * (BM25_K1 + 1) / denom
                )
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        results: List[Dict[str, Any]] = []
        for record_id, score in ranked[: max(1, limit)]:
            doc = self.documents[record_id]
            snippet = doc.text[:300]
            results.append(
                {
                    "record_id": record_id,
                    "score": round(score, 4),
                    "snippet": snippet,
                }
            )
        return results


class KnowledgeStore:
    """Thread-safe registry of BM25 indexes keyed by ``instance:model``."""

    def __init__(self, max_docs: Optional[int] = None) -> None:
        self.max_docs = max_docs or knowledge_max_docs()
        self._indexes: Dict[str, BM25Index] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(instance: str, model: str) -> str:
        return f"{instance}:{model}"

    def total_docs_locked(self) -> int:
        return sum(len(index.documents) for index in self._indexes.values())

    def index_records(
        self,
        instance: str,
        model: str,
        records: List[Dict[str, Any]],
        replace: bool = False,
    ) -> Dict[str, Any]:
        """Add records to the index; refuse additions past the doc budget."""
        key = self._key(instance, model)
        with self._lock:
            if replace:
                self._indexes.pop(key, None)
            index = self._indexes.setdefault(key, BM25Index())
            indexed = 0
            skipped_budget = 0
            for record in records:
                record_id = record.get("id")
                if not isinstance(record_id, int):
                    continue
                if (
                    record_id not in index.documents
                    and self.total_docs_locked() >= self.max_docs
                ):
                    skipped_budget += 1
                    continue
                text = flatten_record_text(record)
                if text.strip():
                    index.add(record_id, text)
                    indexed += 1
            return {
                "success": True,
                "instance": instance,
                "model": model,
                "indexed": indexed,
                "skipped_over_budget": skipped_budget,
                "documents_in_index": len(index.documents),
                "max_documents": self.max_docs,
            }

    def search(
        self, instance: str, model: str, query: str, limit: int = 5
    ) -> Dict[str, Any]:
        key = self._key(instance, model)
        with self._lock:
            index = self._indexes.get(key)
            if index is None or not index.documents:
                return {
                    "success": False,
                    "error": (
                        f"No knowledge index for {model} on instance "
                        f"'{instance}'. Run index_knowledge first."
                    ),
                }
            return {
                "success": True,
                "instance": instance,
                "model": model,
                "query": query,
                "results": index.search(query, limit=limit),
            }

    def drop(self, instance: str, model: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            if model is not None:
                removed = self._indexes.pop(self._key(instance, model), None)
                dropped = 1 if removed else 0
            else:
                prefix = f"{instance}:"
                victims = [key for key in self._indexes if key.startswith(prefix)]
                for key in victims:
                    del self._indexes[key]
                dropped = len(victims)
            return {"success": True, "dropped_indexes": dropped}

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "indexes": [
                    {"key": key, "documents": len(index.documents)}
                    for key, index in sorted(self._indexes.items())
                ],
                "total_documents": self.total_docs_locked(),
                "max_documents": self.max_docs,
            }


_store: Optional[KnowledgeStore] = None
_store_lock = threading.Lock()


def get_knowledge_store() -> KnowledgeStore:
    """Process-wide store, built lazily so env knobs apply at first use."""
    global _store
    with _store_lock:
        if _store is None:
            _store = KnowledgeStore()
        return _store


def reset_knowledge_store() -> None:
    """Drop the process store (intended for tests)."""
    global _store
    with _store_lock:
        _store = None
