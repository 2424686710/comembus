"""Shared-memory lease, refcount, idempotent release, and GC tests."""

from __future__ import annotations

from multiprocessing import shared_memory
import unittest
from unittest import mock

from comembus.object_store.lease_manager import ObjectLeaseManager
from comembus.object_store.lifecycle import ACTIVE, RECLAIMED
from comembus.object_store.shm_store import SharedMemoryObjectStore


class _Clock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class ObjectLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        self.store = SharedMemoryObjectStore()
        self.manager = ObjectLeaseManager(
            self.store, default_lease_seconds=5.0, clock=self.clock
        )
        self.refs = []

    def tearDown(self) -> None:
        self.manager.force_cleanup()

    def _new_object(self):
        ref = self.store.put_bytes(b"lease-test" * 128)
        self.refs.append(ref)
        self.manager.register_object(
            ref,
            owner_agent="owner",
            consumer_agents=["consumer-a", "consumer-b"],
        )
        return ref

    def test_multiple_consumers_and_idempotent_release(self) -> None:
        ref = self._new_object()
        self.assertEqual(self.manager.acquire(ref.object_id, "consumer-a").ref_count, 1)
        self.assertEqual(self.manager.acquire(ref.object_id, "consumer-b").ref_count, 2)
        self.assertEqual(self.manager.release(ref.object_id, "consumer-a").ref_count, 1)
        self.assertEqual(self.manager.release(ref.object_id, "consumer-a").ref_count, 1)
        self.assertEqual(self.manager.get_record(ref.object_id).state, ACTIVE)
        probe = shared_memory.SharedMemory(name=ref.shm_name, create=False)
        probe.close()

    def test_refcount_zero_does_not_unlink_before_expiry(self) -> None:
        ref = self._new_object()
        self.manager.acquire(ref.object_id, "consumer-a")
        self.manager.release(ref.object_id, "consumer-a")
        self.assertEqual(self.manager.collect_expired(), [])
        probe = shared_memory.SharedMemory(name=ref.shm_name, create=False)
        probe.close()
        self.clock.advance(6.0)
        self.assertEqual(self.manager.collect_expired(), [ref.object_id])
        self.assertEqual(self.manager.get_record(ref.object_id).state, RECLAIMED)

    def test_crashed_consumer_is_reclaimed_after_lease_timeout(self) -> None:
        ref = self._new_object()
        self.manager.acquire(ref.object_id, "consumer-a")
        self.clock.advance(6.0)
        self.assertEqual(self.manager.collect_expired(), [ref.object_id])
        stats = self.manager.get_stats()
        self.assertEqual(stats["leaked_object_count"], 1)
        self.assertEqual(stats["reclaimed_object_count"], 1)
        with self.assertRaises(FileNotFoundError):
            shared_memory.SharedMemory(name=ref.shm_name, create=False)

    def test_renew_prevents_early_collection(self) -> None:
        ref = self._new_object()
        self.clock.advance(4.0)
        renewed = self.manager.renew(ref.object_id, lease_seconds=10.0)
        self.assertEqual(renewed.lease_deadline, self.clock.value + 10.0)
        self.clock.advance(2.0)
        self.assertEqual(self.manager.collect_expired(), [])

    def test_gc_unlink_failure_propagates_and_restores_refcount(self) -> None:
        ref = self._new_object()
        self.manager.acquire(ref.object_id, "consumer-a")
        self.clock.advance(6.0)
        with mock.patch.object(
            self.store, "unlink", side_effect=RuntimeError("injected unlink failure")
        ):
            with self.assertRaisesRegex(RuntimeError, "injected unlink failure"):
                self.manager.collect_expired()
        record = self.manager.get_record(ref.object_id)
        self.assertEqual(record.state, ACTIVE)
        self.assertEqual(record.ref_count, 1)


class SharedMemoryExceptionCleanupTests(unittest.TestCase):
    def test_put_failure_unlinks_created_segment_and_reraises(self) -> None:
        class FailingBuffer:
            def __setitem__(self, key, value) -> None:
                raise RuntimeError("injected shared-memory write failure")

        fake_shm = mock.Mock()
        fake_shm.buf = FailingBuffer()
        with mock.patch(
            "comembus.object_store.shm_store.shared_memory.SharedMemory",
            return_value=fake_shm,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "injected shared-memory write failure"
            ):
                SharedMemoryObjectStore().put_bytes(b"payload")
        fake_shm.unlink.assert_called_once_with()
        fake_shm.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main(verbosity=2)
