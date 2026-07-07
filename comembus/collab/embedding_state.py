"""Embedding state exchange helpers for structured collaboration."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import struct
import time
import uuid
from typing import Any, Dict, List, Mapping

from comembus.memory.embedding import HashEmbeddingEncoder


def _vector_bytes(vector: List[float]) -> bytes:
    if not vector:
        return b""
    return struct.pack(f"!{len(vector)}d", *vector)


def compute_checksum(vector: List[float]) -> str:
    return hashlib.sha256(_vector_bytes(vector)).hexdigest()


@dataclass
class EmbeddingState:
    embedding_id: str
    task_id: str
    source_agent: str
    target_agent: str
    summary: str
    vector: List[float] = field(default_factory=list)
    dim: int = 0
    created_at: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "embedding_id": self.embedding_id,
            "task_id": self.task_id,
            "source_agent": self.source_agent,
            "target_agent": self.target_agent,
            "summary": self.summary,
            "vector": list(self.vector),
            "dim": self.dim,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EmbeddingState":
        vector = data.get("vector", [])
        metadata = data.get("metadata", {})
        if not isinstance(vector, list) or not all(
            isinstance(item, (int, float)) for item in vector
        ):
            raise TypeError("vector must be a list of numbers")
        if not isinstance(metadata, dict):
            raise TypeError("metadata must be a dict")
        dim = data.get("dim")
        created_at = data.get("created_at")
        if not isinstance(dim, int):
            raise TypeError("dim must be an integer")
        if dim < 0:
            raise ValueError("dim must be non-negative")
        if len(vector) != dim:
            raise ValueError("dim must match vector length")
        if not isinstance(created_at, (int, float)):
            raise TypeError("created_at must be a number")
        return cls(
            embedding_id=_require_string(data, "embedding_id"),
            task_id=_require_string(data, "task_id"),
            source_agent=_require_string(data, "source_agent"),
            target_agent=_require_string(data, "target_agent"),
            summary=_require_string(data, "summary"),
            vector=[float(item) for item in vector],
            dim=dim,
            created_at=float(created_at),
            metadata=dict(metadata),
        )

    def to_json_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


@dataclass
class EmbeddingRef:
    embedding_id: str
    dim: int
    vector_bytes: int
    checksum: str
    summary: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "embedding_id": self.embedding_id,
            "dim": self.dim,
            "vector_bytes": self.vector_bytes,
            "checksum": self.checksum,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EmbeddingRef":
        dim = data.get("dim")
        vector_bytes = data.get("vector_bytes")
        if not isinstance(dim, int):
            raise TypeError("dim must be an integer")
        if not isinstance(vector_bytes, int):
            raise TypeError("vector_bytes must be an integer")
        if dim < 0 or vector_bytes < 0:
            raise ValueError("dim and vector_bytes must be non-negative")
        return cls(
            embedding_id=_require_string(data, "embedding_id"),
            dim=dim,
            vector_bytes=vector_bytes,
            checksum=_require_string(data, "checksum"),
            summary=_require_string(data, "summary"),
        )

    def to_json_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


def make_embedding_state(
    task_id: str,
    source_agent: str,
    target_agent: str,
    summary: str,
    encoder: HashEmbeddingEncoder,
) -> EmbeddingState:
    if not isinstance(encoder, HashEmbeddingEncoder):
        raise TypeError("encoder must be a HashEmbeddingEncoder")
    vector = encoder.encode(summary)
    return EmbeddingState(
        embedding_id=uuid.uuid4().hex,
        task_id=task_id,
        source_agent=source_agent,
        target_agent=target_agent,
        summary=summary,
        vector=vector,
        dim=len(vector),
        created_at=time.time(),
        metadata={
            "encoder": "HashEmbeddingEncoder",
            "summary_chars": len(summary),
            "non_zero_dimensions": sum(1 for item in vector if item != 0.0),
        },
    )


def make_embedding_ref(state: EmbeddingState) -> EmbeddingRef:
    if not isinstance(state, EmbeddingState):
        raise TypeError("state must be an EmbeddingState")
    vector_payload = _vector_bytes(state.vector)
    return EmbeddingRef(
        embedding_id=state.embedding_id,
        dim=state.dim,
        vector_bytes=len(vector_payload),
        checksum=compute_checksum(state.vector),
        summary=state.summary,
    )


def _require_string(data: Mapping[str, Any], field_name: str) -> str:
    value = data.get(field_name)
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    return value
