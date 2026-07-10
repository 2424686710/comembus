"""Lease, reference-count, and garbage collection for shared-memory objects."""

from __future__ import annotations

from copy import deepcopy
import threading
import time
from typing import Callable, Dict, Iterable, List, Optional, Set

from ..protocol import ObjectRef
from .lifecycle import ACTIVE, FORCE_CLEANED, RECLAIMED, ObjectLifecycleRecord
from .shm_store import SharedMemoryObjectStore


class ObjectLeaseError(Exception):
    """Base object lifecycle error."""


class LeaseObjectNotFoundError(ObjectLeaseError):
    """Raised when a lifecycle operation targets an unknown object."""


class ObjectAlreadyRegisteredError(ObjectLeaseError):
    """Raised when registering a duplicate object ID."""


class ObjectNotActiveError(ObjectLeaseError):
    """Raised when acquiring or renewing a reclaimed object."""


class ObjectLeaseManager:
    """Track object holders and reclaim expired shared-memory allocations."""

    def __init__(
        self,
        object_store: Optional[SharedMemoryObjectStore] = None,
        default_lease_seconds: float = 30.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if default_lease_seconds <= 0:
            raise ValueError("default_lease_seconds must be positive")
        self.object_store = object_store or SharedMemoryObjectStore()
        self.default_lease_seconds = float(default_lease_seconds)
        self._clock = clock
        self._lock = threading.RLock()
        self._records: Dict[str, ObjectLifecycleRecord] = {}
        self._refs: Dict[str, ObjectRef] = {}
        self._holders: Dict[str, Set[str]] = {}
        self._leaked_object_count = 0
        self._reclaimed_object_count = 0

    def register_object(
        self,
        ref: ObjectRef,
        owner_agent: str,
        consumer_agents: Optional[Iterable[str]] = None,
        lease_seconds: Optional[float] = None,
    ) -> ObjectLifecycleRecord:
        if not isinstance(ref, ObjectRef):
            raise TypeError("ref must be an ObjectRef")
        _validate_agent(owner_agent, "owner_agent")
        consumers = _normalize_consumers(consumer_agents or [])
        duration = self._lease_duration(lease_seconds)
        now = self._clock()
        with self._lock:
            if ref.object_id in self._records:
                raise ObjectAlreadyRegisteredError(
                    f"object already registered: {ref.object_id}"
                )
            record = ObjectLifecycleRecord(
                object_id=ref.object_id,
                shm_name=ref.shm_name,
                owner_agent=owner_agent,
                consumer_agents=consumers,
                ref_count=0,
                lease_deadline=now + duration,
                state=ACTIVE,
                created_at=ref.created_at,
                last_access=now,
            )
            self._records[ref.object_id] = record
            self._refs[ref.object_id] = ref
            self._holders[ref.object_id] = set()
            return _clone_record(record)

    def acquire(
        self,
        object_id: str,
        consumer_agent: str,
        lease_seconds: Optional[float] = None,
    ) -> ObjectLifecycleRecord:
        _validate_agent(consumer_agent, "consumer_agent")
        duration = self._lease_duration(lease_seconds)
        now = self._clock()
        with self._lock:
            record = self._active_record(object_id)
            holders = self._holders[object_id]
            holders.add(consumer_agent)
            if consumer_agent not in record.consumer_agents:
                record.consumer_agents.append(consumer_agent)
                record.consumer_agents.sort()
            record.ref_count = len(holders)
            record.lease_deadline = max(record.lease_deadline, now + duration)
            record.last_access = now
            return _clone_record(record)

    def release(self, object_id: str, consumer_agent: str) -> ObjectLifecycleRecord:
        _validate_agent(consumer_agent, "consumer_agent")
        with self._lock:
            record = self._record(object_id)
            if record.state != ACTIVE:
                return _clone_record(record)
            holders = self._holders[object_id]
            holders.discard(consumer_agent)
            record.ref_count = len(holders)
            record.last_access = self._clock()
            return _clone_record(record)

    def renew(
        self,
        object_id: str,
        lease_seconds: Optional[float] = None,
    ) -> ObjectLifecycleRecord:
        duration = self._lease_duration(lease_seconds)
        now = self._clock()
        with self._lock:
            record = self._active_record(object_id)
            record.lease_deadline = now + duration
            record.last_access = now
            return _clone_record(record)

    def collect_expired(self, now: Optional[float] = None) -> List[str]:
        current = self._clock() if now is None else float(now)
        reclaimed: List[str] = []
        with self._lock:
            expired_ids = [
                object_id
                for object_id, record in self._records.items()
                if record.state == ACTIVE and record.lease_deadline <= current
            ]
            for object_id in expired_ids:
                record = self._records[object_id]
                holders = self._holders[object_id]
                leaked_holders = set(holders)
                holders.clear()
                record.ref_count = 0
                # The two required conditions are both true before unlink.
                if record.ref_count == 0 and record.lease_deadline <= current:
                    try:
                        self.object_store.unlink(self._refs[object_id])
                    except Exception:
                        holders.update(leaked_holders)
                        record.ref_count = len(holders)
                        raise
                    if leaked_holders:
                        self._leaked_object_count += 1
                    record.state = RECLAIMED
                    record.last_access = current
                    self._reclaimed_object_count += 1
                    reclaimed.append(object_id)
        return reclaimed

    def force_cleanup(self, object_id: Optional[str] = None) -> List[str]:
        cleaned: List[str] = []
        with self._lock:
            object_ids = [object_id] if object_id is not None else list(self._records)
            for current_id in object_ids:
                record = self._record(current_id)
                if record.state != ACTIVE:
                    continue
                previous_holders = set(self._holders[current_id])
                self._holders[current_id].clear()
                record.ref_count = 0
                try:
                    self.object_store.unlink(self._refs[current_id])
                except Exception:
                    self._holders[current_id].update(previous_holders)
                    record.ref_count = len(previous_holders)
                    raise
                record.state = FORCE_CLEANED
                record.last_access = self._clock()
                self._reclaimed_object_count += 1
                cleaned.append(current_id)
        return cleaned

    def get_record(self, object_id: str) -> ObjectLifecycleRecord:
        with self._lock:
            return _clone_record(self._record(object_id))

    def get_stats(self) -> Dict[str, int]:
        with self._lock:
            active = [record for record in self._records.values() if record.state == ACTIVE]
            return {
                "tracked_object_count": len(self._records),
                "active_object_count": len(active),
                "total_ref_count": sum(record.ref_count for record in active),
                "leaked_object_count": self._leaked_object_count,
                "reclaimed_object_count": self._reclaimed_object_count,
            }

    def close(self, force_cleanup: bool = True) -> None:
        if force_cleanup:
            self.force_cleanup()

    def __enter__(self) -> "ObjectLeaseManager":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close(force_cleanup=True)

    def _record(self, object_id: str) -> ObjectLifecycleRecord:
        if not isinstance(object_id, str) or not object_id:
            raise ValueError("object_id must be a non-empty string")
        record = self._records.get(object_id)
        if record is None:
            raise LeaseObjectNotFoundError(f"object not registered: {object_id}")
        return record

    def _active_record(self, object_id: str) -> ObjectLifecycleRecord:
        record = self._record(object_id)
        if record.state != ACTIVE:
            raise ObjectNotActiveError(
                f"object is not active: {object_id} state={record.state}"
            )
        return record

    def _lease_duration(self, lease_seconds: Optional[float]) -> float:
        duration = (
            self.default_lease_seconds
            if lease_seconds is None
            else float(lease_seconds)
        )
        if duration <= 0:
            raise ValueError("lease_seconds must be positive")
        return duration


def _clone_record(record: ObjectLifecycleRecord) -> ObjectLifecycleRecord:
    return deepcopy(record)


def _validate_agent(agent_id: str, field_name: str) -> None:
    if not isinstance(agent_id, str) or not agent_id:
        raise ValueError(f"{field_name} must be a non-empty string")


def _normalize_consumers(consumers: Iterable[str]) -> List[str]:
    result: Set[str] = set()
    for consumer in consumers:
        _validate_agent(consumer, "consumer_agent")
        result.add(consumer)
    return sorted(result)
