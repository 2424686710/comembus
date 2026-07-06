"""Shared-memory object store tests for CoMemBus."""

from __future__ import annotations

from dataclasses import replace
from multiprocessing import shared_memory
import unittest

from comembus.object_store.shm_store import (
    ChecksumMismatchError,
    ObjectNotFoundError,
    ObjectStoreError,
    SharedMemoryObjectStore,
)


class SharedMemoryObjectStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = SharedMemoryObjectStore()

    def tearDown(self) -> None:
        self.store.close()

    def test_put_get_unlink_round_trip(self) -> None:
        data = b"comembus" * 1024
        ref = self.store.put_bytes(data)
        self.addCleanup(self._safe_unlink, ref)

        self.assertEqual(self.store.get_bytes(ref), data)
        self.store.unlink(ref)

        with self.assertRaises(ObjectNotFoundError):
            self.store.get_bytes(ref)

    def test_checksum_mismatch_raises(self) -> None:
        ref = self.store.put_bytes(b"payload-for-checksum")
        self.addCleanup(self._safe_unlink, ref)

        bad_ref = replace(ref, checksum="0" * 64)
        with self.assertRaises(ChecksumMismatchError):
            self.store.get_bytes(bad_ref)

    def test_unlink_removes_shared_memory_object(self) -> None:
        ref = self.store.put_bytes(b"cleanup-check")
        self.addCleanup(self._safe_unlink, ref)

        self.store.unlink(ref)
        with self.assertRaises(FileNotFoundError):
            shared_memory.SharedMemory(name=ref.shm_name, create=False)

    def _safe_unlink(self, ref) -> None:
        try:
            self.store.unlink(ref)
        except ObjectStoreError:
            pass


if __name__ == "__main__":
    unittest.main(verbosity=2)

