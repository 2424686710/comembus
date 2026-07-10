"""Binary float32 embedding codec and shared-memory exchange tests."""

from __future__ import annotations

from multiprocessing import shared_memory
import unittest

from benchmarks.bench_embedding_codec import benchmark_rows
from comembus.collab.embedding_codec import (
    EmbeddingBinaryCodec,
    EmbeddingCodecError,
)
from comembus.collab.embedding_store import EmbeddingRef, SharedEmbeddingStore
from comembus.memory.embedding import cosine_similarity
from comembus.object_store.shm_store import ChecksumMismatchError


class EmbeddingBinaryCodecTests(unittest.TestCase):
    def test_float32_round_trip_error_and_metadata(self) -> None:
        vector = [0.1, -0.25, 1.0 / 3.0, 0.0, 100.125]
        encoded = EmbeddingBinaryCodec.encode_float32(vector)
        decoded = EmbeddingBinaryCodec.decode_float32(memoryview(encoded), len(vector))
        self.assertEqual(EmbeddingBinaryCodec.dtype, "float32")
        self.assertEqual(len(encoded), len(vector) * 4)
        for expected, actual in zip(vector, decoded):
            self.assertAlmostEqual(expected, actual, places=5)
        self.assertGreaterEqual(cosine_similarity(vector, decoded), 0.999999)
        self.assertEqual(len(EmbeddingBinaryCodec.checksum(encoded)), 64)

    def test_invalid_dimension_size_and_non_finite_values_raise(self) -> None:
        with self.assertRaises(EmbeddingCodecError):
            EmbeddingBinaryCodec.encode_float32([])
        with self.assertRaises(EmbeddingCodecError):
            EmbeddingBinaryCodec.encode_float32([float("nan")])
        with self.assertRaises(EmbeddingCodecError):
            EmbeddingBinaryCodec.decode_float32(b"\0" * 8, dim=3)
        with self.assertRaises(EmbeddingCodecError):
            EmbeddingBinaryCodec.decode_float32(b"", dim=0)
        store = SharedEmbeddingStore()
        ref = store.put_vector((0.1, 0.2))
        try:
            invalid = ref.to_dict()
            invalid["checksum"] = "z" * 64
            with self.assertRaisesRegex(ValueError, "checksum"):
                EmbeddingRef.from_dict(invalid)
        finally:
            store.unlink(ref)
            store.close()

    def test_shared_embedding_ref_memoryview_and_cleanup(self) -> None:
        store = SharedEmbeddingStore()
        vector = [0.01 * index for index in range(64)]
        ref = store.put_vector(vector)
        try:
            restored_ref = EmbeddingRef.from_dict(ref.to_dict())
            self.assertEqual(restored_ref, ref)
            self.assertEqual(ref.dim, 64)
            self.assertEqual(ref.dtype, "float32")
            self.assertEqual(ref.object_ref.size, 64 * 4)
            with store.open_memoryview(ref) as view:
                self.assertIsInstance(view, memoryview)
                decoded = EmbeddingBinaryCodec.decode_float32(view, ref.dim)
            self.assertGreaterEqual(cosine_similarity(vector, decoded), 0.999999)
        finally:
            store.unlink(ref)
            store.close()
        with self.assertRaises(FileNotFoundError):
            shared_memory.SharedMemory(name=ref.object_ref.shm_name, create=False)

    def test_checksum_corruption_is_rejected_and_still_cleanupable(self) -> None:
        store = SharedEmbeddingStore()
        ref = store.put_vector([0.1, 0.2, 0.3, 0.4])
        shm = shared_memory.SharedMemory(name=ref.object_ref.shm_name, create=False)
        try:
            shm.buf[0] ^= 0xFF
        finally:
            shm.close()
        try:
            with self.assertRaises(ChecksumMismatchError):
                store.get_vector(ref)
        finally:
            store.unlink(ref)
            store.close()

    def test_small_benchmark_checksums_and_similarity(self) -> None:
        rows = benchmark_rows(dimensions=(32, 64), rounds=2, warmup=0)
        self.assertEqual(len(rows), 16)
        self.assertTrue(all(row["checksum_ok"] for row in rows))
        vector_rows = [row for row in rows if row["mode"] != "summary_text"]
        self.assertTrue(all(row["cosine_similarity_preserved"] for row in vector_rows))
        summary = [row for row in rows if row["mode"] == "summary_text"]
        self.assertTrue(all(not row["cosine_similarity_preserved"] for row in summary))


if __name__ == "__main__":
    unittest.main(verbosity=2)
