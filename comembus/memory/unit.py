"""Memory dataclasses for the shared blackboard."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from typing import Any, Dict, List, Mapping

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
        )

    def to_json_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


@dataclass(frozen=True)
class MemorySearchResult:
    """A scored blackboard search result."""

    memory: MemoryUnit
    score: float
    reason: str


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

