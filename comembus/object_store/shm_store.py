"""Shared-memory object storage for large payloads."""

from __future__ import annotations

from hashlib import sha256
from multiprocessing import shared_memory
import time
import uuid
from typing import Optional

from ..metrics.recorder import MetricsRecorder
from ..protocol import ObjectRef


class ObjectStoreError(Exception):
    """Base shared-memory object store error."""


class ObjectNotFoundError(ObjectStoreError):
    """Raised when a shared-memory object cannot be found."""


class ChecksumMismatchError(ObjectStoreError):
    """Raised when the retrieved bytes do not match the advertised checksum."""


class SharedMemoryObjectStore:
    """Store byte payloads in POSIX shared memory."""

    def __init__(self, metrics_recorder: Optional[MetricsRecorder] = None) -> None:
        self.metrics_recorder = metrics_recorder

    def put_bytes(self, data: bytes) -> ObjectRef:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("data must be bytes-like")
        raw = bytes(data)
        if not raw:
            raise ValueError("data must not be empty")

        object_id = uuid.uuid4().hex
        shm_name = f"comembus_{object_id}"
        shm = shared_memory.SharedMemory(name=shm_name, create=True, size=len(raw))
        try:
            shm.buf[: len(raw)] = raw
        finally:
            shm.close()
        if self.metrics_recorder is not None:
            self.metrics_recorder.record_shm_write(len(raw))

        return ObjectRef(
            object_id=object_id,
            shm_name=shm_name,
            size=len(raw),
            checksum=sha256(raw).hexdigest(),
            created_at=time.time(),
        )

    def get_bytes(self, ref: ObjectRef) -> bytes:
        shm = self._attach(ref)
        try:
            data = bytes(shm.buf[: ref.size])
        finally:
            shm.close()
        if self.metrics_recorder is not None:
            self.metrics_recorder.record_shm_read(len(data))

        checksum = sha256(data).hexdigest()
        if checksum != ref.checksum:
            raise ChecksumMismatchError(
                f"checksum mismatch for object {ref.object_id}: "
                f"{checksum} != {ref.checksum}"
            )
        return data

    def unlink(self, ref: ObjectRef) -> None:
        shm = self._attach(ref)
        try:
            shm.unlink()
        finally:
            shm.close()

    def close(self) -> None:
        """Provided for API symmetry; this store closes shared memory per call."""

    def _attach(self, ref: ObjectRef) -> shared_memory.SharedMemory:
        if ref.size <= 0:
            raise ObjectStoreError("object size must be positive")
        try:
            return shared_memory.SharedMemory(name=ref.shm_name, create=False)
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(
                f"shared memory object not found: {ref.shm_name}"
            ) from exc
