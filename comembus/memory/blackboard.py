"""Shared blackboard with keyword, tag, and semantic search."""

from __future__ import annotations

import time
import uuid
from typing import Dict, Iterable, List, Sequence

from .embedding import HashEmbeddingEncoder, cosine_similarity
from .sqlite_store import SQLiteMemoryStore
from .unit import MemorySearchResult, MemoryUnit
from .provenance import MemoryProvenance, build_provenance


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
        version: int | None = None,
        valid_from: float | None = None,
        expires_at: float | None = None,
        ttl_seconds: float | None = None,
        parent_memory_ids: List[str] | None = None,
        supersedes_memory_id: str = "",
        provenance: Dict | None = None,
    ) -> MemoryUnit:
        now = time.time()
        if expires_at is not None and ttl_seconds is not None:
            raise ValueError("provide expires_at or ttl_seconds, not both")
        resolved_valid_from = now if valid_from is None else float(valid_from)
        resolved_expires_at = expires_at
        if ttl_seconds is not None:
            if ttl_seconds <= 0:
                raise ValueError("ttl_seconds must be positive")
            resolved_expires_at = resolved_valid_from + float(ttl_seconds)
        parents = list(parent_memory_ids or [])
        parent_memory = None
        if supersedes_memory_id:
            if supersedes_memory_id not in parents:
                parents.append(supersedes_memory_id)
            parent_memory = self._store.get(supersedes_memory_id)
            if parent_memory is None:
                raise KeyError(f"memory not found: {supersedes_memory_id}")
        resolved_version = (
            int(version)
            if version is not None
            else (parent_memory.version + 1 if parent_memory is not None else 1)
        )
        resolved_provenance = (
            MemoryProvenance.from_dict(provenance).to_dict()
            if provenance is not None
            else build_provenance(
                source_task_id=task_id,
                source_agent=source_agent,
                evidence_memory_ids=parents,
                recorded_at=now,
            )
        )
        memory = MemoryUnit(
            memory_id=uuid.uuid4().hex,
            task_id=task_id,
            source_agent=source_agent,
            created_at=now,
            task_topic=task_topic,
            memory_type=memory_type,
            summary=summary,
            content=content,
            tags=list(tags or []),
            confidence=float(confidence),
            metadata=dict(metadata or {}),
            version=resolved_version,
            valid_from=resolved_valid_from,
            expires_at=(
                None if resolved_expires_at is None else float(resolved_expires_at)
            ),
            parent_memory_ids=parents,
            provenance=resolved_provenance,
        )
        embedding = self._encoder.encode(f"{summary}\n{content}")
        stored = self._store.put(memory, embedding)
        if supersedes_memory_id and stored.memory_id == memory.memory_id:
            self._store.mark_superseded(supersedes_memory_id, stored.memory_id)
        return stored

    def get_memory(self, memory_id: str) -> MemoryUnit | None:
        return self._store.get(memory_id)

    def list_task_memories(self, task_id: str) -> List[MemoryUnit]:
        return self._store.list_by_task(task_id)

    def list_memories(
        self,
        active_only: bool = False,
        at_time: float | None = None,
    ) -> List[MemoryUnit]:
        return self._store.list_all(active_only=active_only, at_time=at_time)

    def search_by_keyword(
        self,
        query: str,
        top_k: int = 5,
        at_time: float | None = None,
    ) -> List[MemorySearchResult]:
        query_tokens = set(self._encoder.tokenize(query))
        if not query_tokens:
            return []

        results: List[MemorySearchResult] = []
        for record in self._store.list_all_records(active_only=True, at_time=at_time):
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

    def search_by_tag(
        self,
        tags: List[str],
        top_k: int = 5,
        at_time: float | None = None,
    ) -> List[MemorySearchResult]:
        normalized_tags = {tag.strip().lower() for tag in tags if tag.strip()}
        if not normalized_tags:
            return []

        results: List[MemorySearchResult] = []
        for memory in self._store.list_all(active_only=True, at_time=at_time):
            memory_tags = {tag.lower() for tag in memory.tags}
            matched = normalized_tags & memory_tags
            if not matched:
                continue
            score = 3.0 * len(matched)
            reason = f"tag({','.join(sorted(matched))})"
            results.append(MemorySearchResult(memory=memory, score=score, reason=reason))
        return _top_results(results, top_k)

    def search_semantic(
        self,
        query: str,
        top_k: int = 5,
        at_time: float | None = None,
    ) -> List[MemorySearchResult]:
        query_embedding = self._encoder.encode(query)
        if all(value == 0.0 for value in query_embedding):
            return []

        results: List[MemorySearchResult] = []
        for record in self._store.list_all_records(active_only=True, at_time=at_time):
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
        at_time: float | None = None,
    ) -> List[MemorySearchResult]:
        combined: Dict[str, MemorySearchResult] = {}
        for result in self.search_by_keyword(
            query, top_k=max(top_k * 2, top_k), at_time=at_time
        ):
            _merge_result(combined, result)
        for result in self.search_by_tag(
            tags or [], top_k=max(top_k * 2, top_k), at_time=at_time
        ):
            _merge_result(combined, result)
        for result in self.search_semantic(
            query, top_k=max(top_k * 2, top_k), at_time=at_time
        ):
            _merge_result(combined, result)
        return _top_results(combined.values(), top_k)

    def close(self) -> None:
        self._store.close()

    def supersede_memory(self, memory_id: str, superseded_by: str) -> None:
        self._store.mark_superseded(memory_id, superseded_by)


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
