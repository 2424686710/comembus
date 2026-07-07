"""Shared blackboard with keyword, tag, and semantic search."""

from __future__ import annotations

import time
import uuid
from typing import Dict, Iterable, List, Sequence

from .embedding import HashEmbeddingEncoder, cosine_similarity
from .sqlite_store import SQLiteMemoryStore
from .unit import MemorySearchResult, MemoryUnit


class SharedBlackboard:
    """Persist and retrieve reusable agent memories."""

    def __init__(self, db_path: str, embedding_dim: int = 128) -> None:
        self._encoder = HashEmbeddingEncoder(dim=embedding_dim)
        self._store = SQLiteMemoryStore(db_path=db_path)

    def write_memory(
        self,
        task_id: str,
        source_agent: str,
        task_topic: str,
        memory_type: str,
        summary: str,
        content: str,
        tags: List[str] | None = None,
        confidence: float = 1.0,
        metadata: Dict | None = None,
    ) -> MemoryUnit:
        memory = MemoryUnit(
            memory_id=uuid.uuid4().hex,
            task_id=task_id,
            source_agent=source_agent,
            created_at=time.time(),
            task_topic=task_topic,
            memory_type=memory_type,
            summary=summary,
            content=content,
            tags=list(tags or []),
            confidence=float(confidence),
            metadata=dict(metadata or {}),
        )
        embedding = self._encoder.encode(f"{summary}\n{content}")
        self._store.put(memory, embedding)
        return memory

    def get_memory(self, memory_id: str) -> MemoryUnit | None:
        return self._store.get(memory_id)

    def list_task_memories(self, task_id: str) -> List[MemoryUnit]:
        return self._store.list_by_task(task_id)

    def search_by_keyword(self, query: str, top_k: int = 5) -> List[MemorySearchResult]:
        query_tokens = set(self._encoder.tokenize(query))
        if not query_tokens:
            return []

        results: List[MemorySearchResult] = []
        for record in self._store.list_all_records():
            memory = record["memory"]
            summary_tokens = set(self._encoder.tokenize(memory.summary))
            content_tokens = set(self._encoder.tokenize(memory.content))
            summary_hits = query_tokens & summary_tokens
            content_hits = query_tokens & content_tokens
            if not summary_hits and not content_hits:
                continue
            score = (2.0 * len(summary_hits)) + float(len(content_hits))
            reason_parts: List[str] = []
            if summary_hits:
                reason_parts.append(f"keyword:summary({','.join(sorted(summary_hits))})")
            if content_hits:
                reason_parts.append(f"keyword:content({','.join(sorted(content_hits))})")
            results.append(
                MemorySearchResult(memory=memory, score=score, reason=";".join(reason_parts))
            )
        return _top_results(results, top_k)

    def search_by_tag(self, tags: List[str], top_k: int = 5) -> List[MemorySearchResult]:
        normalized_tags = {tag.strip().lower() for tag in tags if tag.strip()}
        if not normalized_tags:
            return []

        results: List[MemorySearchResult] = []
        for memory in self._store.list_all():
            memory_tags = {tag.lower() for tag in memory.tags}
            matched = normalized_tags & memory_tags
            if not matched:
                continue
            score = 3.0 * len(matched)
            reason = f"tag({','.join(sorted(matched))})"
            results.append(MemorySearchResult(memory=memory, score=score, reason=reason))
        return _top_results(results, top_k)

    def search_semantic(self, query: str, top_k: int = 5) -> List[MemorySearchResult]:
        query_embedding = self._encoder.encode(query)
        if all(value == 0.0 for value in query_embedding):
            return []

        results: List[MemorySearchResult] = []
        for record in self._store.list_all_records():
            memory = record["memory"]
            similarity = cosine_similarity(query_embedding, record["embedding"])
            if similarity <= 0.0:
                continue
            results.append(
                MemorySearchResult(
                    memory=memory,
                    score=similarity,
                    reason=f"semantic({similarity:.4f})",
                )
            )
        return _top_results(results, top_k)

    def search(
        self,
        query: str,
        tags: List[str] | None = None,
        top_k: int = 5,
    ) -> List[MemorySearchResult]:
        combined: Dict[str, MemorySearchResult] = {}
        for result in self.search_by_keyword(query, top_k=max(top_k * 2, top_k)):
            _merge_result(combined, result)
        for result in self.search_by_tag(tags or [], top_k=max(top_k * 2, top_k)):
            _merge_result(combined, result)
        for result in self.search_semantic(query, top_k=max(top_k * 2, top_k)):
            _merge_result(combined, result)
        return _top_results(combined.values(), top_k)

    def close(self) -> None:
        self._store.close()


def _merge_result(
    results: Dict[str, MemorySearchResult],
    candidate: MemorySearchResult,
) -> None:
    existing = results.get(candidate.memory.memory_id)
    if existing is None:
        results[candidate.memory.memory_id] = candidate
        return
    reason_parts = set(filter(None, existing.reason.split(";"))) | set(
        filter(None, candidate.reason.split(";"))
    )
    results[candidate.memory.memory_id] = MemorySearchResult(
        memory=candidate.memory,
        score=existing.score + candidate.score,
        reason=";".join(sorted(reason_parts)),
    )


def _top_results(
    results: Iterable[MemorySearchResult],
    top_k: int,
) -> List[MemorySearchResult]:
    if top_k <= 0:
        return []
    ordered = sorted(
        results,
        key=lambda item: (-item.score, item.memory.created_at, item.memory.memory_id),
    )
    return ordered[:top_k]

