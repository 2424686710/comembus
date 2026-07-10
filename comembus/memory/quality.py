"""Labeled retrieval quality metrics, including wrong and stale reuse."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Dict, Iterable, List, Sequence, Set

from .ranking import MemoryRanker
from .unit import MemoryUnit


@dataclass(frozen=True)
class RetrievalQualityQuery:
    query_id: str
    text: str
    tags: List[str]
    relevant_memory_ids: Set[str]
    stale_memory_ids: Set[str] = field(default_factory=set)


def evaluate_retrieval_quality(
    method: str,
    queries: Sequence[RetrievalQualityQuery],
    memories: Iterable[MemoryUnit],
    ranker: MemoryRanker | None = None,
    at_time: float | None = None,
) -> Dict[str, Any]:
    if not queries:
        raise ValueError("at least one quality query is required")
    corpus = list(memories)
    active_ranker = ranker or MemoryRanker()
    precision_1 = 0.0
    precision_3 = 0.0
    recall_1 = 0.0
    recall_3 = 0.0
    reciprocal_rank = 0.0
    wrong_reuse_count = 0
    task_success_count = 0
    stale_total = 0
    stale_rejected = 0
    latencies: List[float] = []

    for query in queries:
        started = time.perf_counter()
        results = active_ranker.rank(
            method,
            query.text,
            query.tags,
            corpus,
            top_k=max(3, len(corpus)),
            at_time=at_time,
        )
        latencies.append((time.perf_counter() - started) * 1000.0)
        result_ids = [result.memory.memory_id for result in results]
        top_1 = result_ids[:1]
        top_3 = result_ids[:3]
        relevant = query.relevant_memory_ids
        if not relevant:
            raise ValueError(f"query has no relevant memories: {query.query_id}")
        hits_1 = len(set(top_1) & relevant)
        hits_3 = len(set(top_3) & relevant)
        precision_1 += hits_1
        precision_3 += hits_3 / 3.0
        recall_1 += hits_1 / len(relevant)
        recall_3 += hits_3 / len(relevant)
        first_relevant_rank = next(
            (index for index, memory_id in enumerate(result_ids, start=1) if memory_id in relevant),
            None,
        )
        if first_relevant_rank is not None:
            reciprocal_rank += 1.0 / first_relevant_rank
        if top_1 and top_1[0] not in relevant:
            wrong_reuse_count += 1
        if top_1 and top_1[0] in relevant:
            task_success_count += 1
        stale_total += len(query.stale_memory_ids)
        stale_rejected += len(query.stale_memory_ids - set(result_ids))

    count = len(queries)
    return {
        "method": method,
        "query_count": count,
        "precision_at_1": precision_1 / count,
        "precision_at_3": precision_3 / count,
        "recall_at_1": recall_1 / count,
        "recall_at_3": recall_3 / count,
        "mrr": reciprocal_rank / count,
        "wrong_reuse_rate": wrong_reuse_count / count,
        "stale_memory_rejection_rate": (
            stale_rejected / stale_total if stale_total else 1.0
        ),
        "query_latency_ms": sum(latencies) / len(latencies),
        "task_success_rate": task_success_count / count,
    }
