"""Tests for embedding direct exchange helpers."""

from __future__ import annotations

import os
import tempfile
import unittest

from comembus.collab.embedding_state import (
    EmbeddingRef,
    EmbeddingState,
    compute_checksum,
    make_embedding_ref,
    make_embedding_state,
)
from comembus.collab.structured_mode import StructuredCollaborationRunner
from comembus.memory.embedding import HashEmbeddingEncoder


class EmbeddingExchangeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="comembus-embedding-test-")
        self.db_path = os.path.join(self.tempdir.name, "embedding.sqlite")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_embedding_state_round_trip(self) -> None:
        state = EmbeddingState(
            embedding_id="emb-1",
            task_id="task-1",
            source_agent="log-agent",
            target_agent="review-agent",
            summary="database timeout summary",
            vector=[0.5, -0.5],
            dim=2,
            created_at=1.0,
            metadata={"encoder": "hash"},
        )

        restored = EmbeddingState.from_dict(state.to_dict())

        self.assertEqual(restored.to_dict(), state.to_dict())
        self.assertEqual(restored.to_json_bytes(), state.to_json_bytes())

    def test_embedding_ref_checksum_matches_vector(self) -> None:
        state = EmbeddingState(
            embedding_id="emb-2",
            task_id="task-2",
            source_agent="log-agent",
            target_agent="review-agent",
            summary="permission denied summary",
            vector=[0.25, 0.75, -0.25],
            dim=3,
            created_at=2.0,
            metadata={},
        )

        ref = make_embedding_ref(state)

        self.assertEqual(ref.checksum, compute_checksum(state.vector))
        self.assertEqual(EmbeddingRef.from_dict(ref.to_dict()).to_dict(), ref.to_dict())

    def test_make_embedding_state_is_deterministic_for_same_summary(self) -> None:
        encoder = HashEmbeddingEncoder(dim=64)

        first = make_embedding_state("task-3", "log-agent", "review-agent", "storage full", encoder)
        second = make_embedding_state(
            "task-3",
            "log-agent",
            "review-agent",
            "storage full",
            encoder,
        )

        self.assertEqual(first.dim, 64)
        self.assertTrue(any(value != 0.0 for value in first.vector))
        self.assertEqual(compute_checksum(first.vector), compute_checksum(second.vector))

    def test_structured_mode_reports_embedding_state_metrics(self) -> None:
        metrics = StructuredCollaborationRunner(
            task_index=1,
            task_topic="database timeout",
            db_path=self.db_path,
        ).run()

        self.assertGreaterEqual(metrics.embedding_state_count, 1)
        self.assertGreater(metrics.embedding_state_bytes, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
