"""Retrieval quality, hard-negative, wrong reuse, and stale filtering tests."""

from __future__ import annotations

import os
import tempfile
import unittest

from benchmarks.bench_memory_quality import benchmark_rows
from comembus.memory.quality import RetrievalQualityQuery, evaluate_retrieval_quality
from comembus.memory.ranking import MemoryRanker
from comembus.memory.unit import MemoryUnit


def _memory(
    memory_id: str,
    summary: str,
    tags: list[str],
    expires_at=None,
    superseded_by: str = "",
) -> MemoryUnit:
    return MemoryUnit(
        memory_id=memory_id,
        task_id=f"task-{memory_id}",
        source_agent="review-agent",
        created_at=10.0,
        task_topic=summary,
        memory_type="strategy",
        summary=summary,
        content=summary,
        tags=tags,
        confidence=0.9,
        metadata={},
        valid_from=1.0,
        expires_at=expires_at,
        superseded_by=superseded_by,
    )


class MemoryQualityTests(unittest.TestCase):
    def test_expired_and_superseded_memories_are_not_ranked(self) -> None:
        active = _memory("active", "database timeout wrong port", ["wrong_port"])
        expired = _memory(
            "expired", "database timeout wrong port", ["wrong_port"], expires_at=5.0
        )
        superseded = _memory(
            "superseded",
            "database timeout wrong port",
            ["wrong_port"],
            superseded_by="active",
        )
        ranker = MemoryRanker(embedding_dim=128)
        ranked = ranker.rank(
            "hybrid",
            "database timeout wrong port",
            ["wrong_port"],
            [active, expired, superseded],
            top_k=3,
            at_time=10.0,
        )
        self.assertEqual([result.memory.memory_id for result in ranked], ["active"])

    def test_quality_metrics_explicitly_count_wrong_reuse(self) -> None:
        correct = _memory("correct", "database wrong port", ["wrong_port"])
        hard = _memory(
            "hard", "database timeout connection pool failure root cause", ["pool"]
        )
        query = RetrievalQualityQuery(
            query_id="q1",
            text="database timeout connection pool root cause",
            tags=["wrong_port"],
            relevant_memory_ids={"correct"},
        )
        keyword = evaluate_retrieval_quality(
            "keyword_only", [query], [correct, hard], at_time=10.0
        )
        hybrid = evaluate_retrieval_quality(
            "hybrid", [query], [correct, hard], at_time=10.0
        )
        self.assertEqual(keyword["wrong_reuse_rate"], 1.0)
        self.assertEqual(hybrid["wrong_reuse_rate"], 0.0)
        self.assertEqual(hybrid["mrr"], 1.0)

    def test_full_quality_benchmark_meets_hybrid_and_stale_requirements(self) -> None:
        with tempfile.TemporaryDirectory(prefix="comembus-quality-test-") as directory:
            rows = benchmark_rows(os.path.join(directory, "quality.sqlite"))
        self.assertEqual(len(rows), 4)
        by_method = {row["method"]: row for row in rows}
        hybrid_mrr = float(by_method["hybrid"]["mrr"])
        best_single = max(
            float(row["mrr"])
            for method, row in by_method.items()
            if method != "hybrid"
        )
        self.assertGreaterEqual(hybrid_mrr + 0.01, best_single)
        self.assertTrue(
            all(float(row["stale_memory_rejection_rate"]) == 1.0 for row in rows)
        )
        self.assertGreater(float(by_method["keyword_only"]["wrong_reuse_rate"]), 0)
        self.assertTrue(all(row["dedup_verified"] for row in rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
