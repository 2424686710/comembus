"""Memory dataclasses for the shared blackboard."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
import json
import time
from typing import Any, Dict, List, Mapping, Optional

ALLOWED_MEMORY_TYPES = {"fact", "evidence", "summary", "strategy", "error", "artifact"}


@dataclass
class MemoryUnit:
    """A persistent blackboard memory entry."""

    memory_id: str
    task_id: str
    source_agent: str
    created_at: float
    task_topic: str
    memory_type: str
    summary: str
    content: str
    tags: List[str] = field(default_factory=list)
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""
    version: int = 1
    valid_from: float = 0.0
    expires_at: Optional[float] = None
    parent_memory_ids: List[str] = field(default_factory=list)
    superseded_by: str = ""
    provenance: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        expected_content_hash = compute_content_hash(self.content)
        if not self.content_hash:
            self.content_hash = expected_content_hash
        elif self.content_hash != expected_content_hash:
            raise ValueError("content_hash does not match content")
        if self.version <= 0:
            raise ValueError("version must be positive")
        if self.valid_from == 0.0:
            self.valid_from = float(self.created_at)
        if self.expires_at is not None and self.expires_at < self.valid_from:
            raise ValueError("expires_at must not be earlier than valid_from")
        if not all(isinstance(item, str) and item for item in self.parent_memory_ids):
            raise ValueError("parent_memory_ids must contain non-empty strings")
        if not isinstance(self.provenance, dict):
            raise TypeError("provenance must be a dict")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "task_id": self.task_id,
            "source_agent": self.source_agent,
            "created_at": self.created_at,
            "task_topic": self.task_topic,
            "memory_type": self.memory_type,
            "summary": self.summary,
            "content": self.content,
            "tags": list(self.tags),
            "confidence": self.confidence,
            "metadata": deepcopy(self.metadata),
            "content_hash": self.content_hash,
            "version": self.version,
            "valid_from": self.valid_from,
            "expires_at": self.expires_at,
            "parent_memory_ids": list(self.parent_memory_ids),
            "superseded_by": self.superseded_by,
            "provenance": deepcopy(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MemoryUnit":
        if not isinstance(data, Mapping):
            raise TypeError("memory unit must be a mapping")

        memory_id = _require_string(data, "memory_id")
        task_id = _require_string(data, "task_id")
        source_agent = _require_string(data, "source_agent")
        created_at = _require_float(data, "created_at")
        task_topic = _require_string(data, "task_topic")
        memory_type = _require_string(data, "memory_type")
        if memory_type not in ALLOWED_MEMORY_TYPES:
            raise ValueError(f"unsupported memory_type: {memory_type}")
        summary = _require_string(data, "summary")
        content = _require_string(data, "content")
        tags = _require_string_list(data, "tags")
        confidence = _require_float(data, "confidence")
        metadata = _require_dict(data, "metadata")
        content_hash = data.get("content_hash", "")
        version = data.get("version", 1)
        valid_from = data.get("valid_from", created_at)
        expires_at = data.get("expires_at")
        parent_memory_ids = data.get("parent_memory_ids", [])
        superseded_by = data.get("superseded_by", "")
        provenance = data.get("provenance", {})
        if not isinstance(content_hash, str):
            raise TypeError("content_hash must be a string")
        if not isinstance(version, int) or version <= 0:
            raise ValueError("version must be a positive integer")
        if not isinstance(valid_from, (int, float)):
            raise TypeError("valid_from must be a number")
        if expires_at is not None and not isinstance(expires_at, (int, float)):
            raise TypeError("expires_at must be a number or null")
        if not isinstance(parent_memory_ids, list) or not all(
            isinstance(item, str) for item in parent_memory_ids
        ):
            raise TypeError("parent_memory_ids must be a list of strings")
        if not isinstance(superseded_by, str):
            raise TypeError("superseded_by must be a string")
        if not isinstance(provenance, dict):
            raise TypeError("provenance must be a dict")

        return cls(
            memory_id=memory_id,
            task_id=task_id,
            source_agent=source_agent,
            created_at=created_at,
            task_topic=task_topic,
            memory_type=memory_type,
            summary=summary,
            content=content,
            tags=tags,
            confidence=confidence,
            metadata=metadata,
            content_hash=content_hash,
            version=version,
            valid_from=float(valid_from),
            expires_at=None if expires_at is None else float(expires_at),
            parent_memory_ids=list(parent_memory_ids),
            superseded_by=superseded_by,
            provenance=deepcopy(provenance),
        )

    def to_json_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def is_reusable(self, at_time: float | None = None) -> bool:
        current = time.time() if at_time is None else float(at_time)
        return (
            self.valid_from <= current
            and (self.expires_at is None or self.expires_at > current)
            and not self.superseded_by
        )

    def is_expired(self, at_time: float | None = None) -> bool:
        current = time.time() if at_time is None else float(at_time)
        return self.expires_at is not None and self.expires_at <= current


@dataclass(frozen=True)
class MemorySearchResult:
    """A scored blackboard search result."""

    memory: MemoryUnit
    score: float
    reason: str


def compute_content_hash(content: str) -> str:
    if not isinstance(content, str):
        raise TypeError("content must be a string")
    return sha256(content.encode("utf-8")).hexdigest()


def _require_string(data: Mapping[str, Any], field_name: str) -> str:
    value = data.get(field_name)
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    return value


def _require_float(data: Mapping[str, Any], field_name: str) -> float:
    value = data.get(field_name)
    if not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    return float(value)


def _require_string_list(data: Mapping[str, Any], field_name: str) -> List[str]:
    value = data.get(field_name)
    if not isinstance(value, list):
        raise TypeError(f"{field_name} must be a list")
    if not all(isinstance(item, str) for item in value):
        raise TypeError(f"{field_name} must only contain strings")
    return list(value)


def _require_dict(data: Mapping[str, Any], field_name: str) -> Dict[str, Any]:
    value = data.get(field_name)
    if not isinstance(value, dict):
        raise TypeError(f"{field_name} must be a dict")
    return deepcopy(value)
