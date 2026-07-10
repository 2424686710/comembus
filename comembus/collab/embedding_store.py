"""Shared-memory storage for binary float32 embedding vectors."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
from multiprocessing import shared_memory
from typing import Any, Dict, Iterator, Mapping, Optional

from comembus.metrics.recorder import MetricsRecorder
from comembus.object_store.shm_store import (
    ChecksumMismatchError,
    ObjectNotFoundError,
    SharedMemoryObjectStore,
)
from comembus.protocol import ObjectRef

from .embedding_codec import EmbeddingBinaryCodec, EmbeddingCodecError


@dataclass(frozen=True)
class EmbeddingRef:
    """Control-plane metadata for a binary vector in shared memory."""

    object_ref: ObjectRef
    dim: int
    dtype: str
    checksum: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "object_ref": self.object_ref.to_dict(),
            "dim": self.dim,
            "dtype": self.dtype,
            "checksum": self.checksum,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EmbeddingRef":
        if not isinstance(data, Mapping):
            raise TypeError("embedding ref must be a mapping")
        dim = data.get("dim")
        dtype = data.get("dtype")
        checksum = data.get("checksum")
        object_ref = data.get("object_ref")
        if not isinstance(dim, int) or dim <= 0:
            raise ValueError("dim must be a positive integer")
        if dtype != EmbeddingBinaryCodec.dtype:
            raise ValueError(f"unsupported embedding dtype: {dtype}")
        if not isinstance(checksum, str) or len(checksum) != 64:
            raise ValueError("checksum must be a SHA-256 hex string")
        try:
            int(checksum, 16)
        except ValueError as exc:
            raise ValueError("checksum must be a SHA-256 hex string") from exc
        if not isinstance(object_ref, Mapping):
            raise TypeError("object_ref must be a mapping")
        ref = ObjectRef.from_dict(object_ref)
        if ref.size != dim * EmbeddingBinaryCodec.item_size:
            raise ValueError("ObjectRef size does not match float32 dim")
        return cls(
            object_ref=ref,
            dim=dim,
            dtype=dtype,
            checksum=checksum,
        )

    def to_json_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(), separators=(",", ":"), sort_keys=True
        ).encode("utf-8")


class SharedEmbeddingStore:
    """Write float32 vectors once and decode directly from a shared buffer view."""

    def __init__(self, metrics_recorder: Optional[MetricsRecorder] = None) -> None:
        self.metrics_recorder = metrics_recorder
        self.object_store = SharedMemoryObjectStore(metrics_recorder)

    def put_vector(self, vector: list[float] | tuple[float, ...]) -> EmbeddingRef:
        values = [float(value) for value in vector]
        payload = EmbeddingBinaryCodec.encode_float32(values)
        checksum = EmbeddingBinaryCodec.checksum(payload)
        object_ref = self.object_store.put_bytes(payload)
        try:
            return EmbeddingRef(
                object_ref=object_ref,
                dim=len(values),
                dtype=EmbeddingBinaryCodec.dtype,
                checksum=checksum,
            )
        except Exception:
            self.object_store.unlink(object_ref)
            raise

    def get_vector(self, ref: EmbeddingRef) -> list[float]:
        with self.open_memoryview(ref) as view:
            return EmbeddingBinaryCodec.decode_float32(view, ref.dim)

    @contextmanager
    def open_memoryview(self, ref: EmbeddingRef) -> Iterator[memoryview]:
        self._validate_ref(ref)
        try:
            shm = shared_memory.SharedMemory(
                name=ref.object_ref.shm_name, create=False
            )
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(
                f"shared embedding not found: {ref.object_ref.shm_name}"
            ) from exc
        view: Optional[memoryview] = None
        try:
            view = shm.buf[: ref.object_ref.size]
            checksum = EmbeddingBinaryCodec.checksum(view)
            if checksum != ref.checksum or checksum != ref.object_ref.checksum:
                raise ChecksumMismatchError(
                    f"embedding checksum mismatch: {checksum} != {ref.checksum}"
                )
            if self.metrics_recorder is not None:
                self.metrics_recorder.record_shm_read(len(view))
            yield view
        finally:
            if view is not None:
                view.release()
            shm.close()

    # Readability aliases for callers that prefer a shorter context-manager name.
    memoryview = open_memoryview
    read_memoryview = open_memoryview

    def unlink(self, ref: EmbeddingRef) -> None:
        self._validate_ref(ref)
        self.object_store.unlink(ref.object_ref)

    def close(self) -> None:
        self.object_store.close()

    @staticmethod
    def _validate_ref(ref: EmbeddingRef) -> None:
        if not isinstance(ref, EmbeddingRef):
            raise TypeError("ref must be an EmbeddingRef")
        if ref.dtype != EmbeddingBinaryCodec.dtype:
            raise EmbeddingCodecError(f"unsupported embedding dtype: {ref.dtype}")
        if ref.object_ref.size != ref.dim * EmbeddingBinaryCodec.item_size:
            raise EmbeddingCodecError("embedding ObjectRef size does not match dim")
