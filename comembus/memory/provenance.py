"""Typed provenance metadata for reusable CoMemBus memories."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Dict, Iterable, List, Mapping


@dataclass(frozen=True)
class MemoryProvenance:
    source_task_id: str
    source_agent: str
    evidence_memory_ids: List[str] = field(default_factory=list)
    derivation: str = "direct_observation"
    recorded_at: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_task_id": self.source_task_id,
            "source_agent": self.source_agent,
            "evidence_memory_ids": list(self.evidence_memory_ids),
            "derivation": self.derivation,
            "recorded_at": self.recorded_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MemoryProvenance":
        if not isinstance(data, Mapping):
            raise TypeError("provenance must be a mapping")
        evidence = data.get("evidence_memory_ids", [])
        metadata = data.get("metadata", {})
        if not isinstance(evidence, list) or not all(
            isinstance(item, str) for item in evidence
        ):
            raise TypeError("evidence_memory_ids must be a list of strings")
        if not isinstance(metadata, dict):
            raise TypeError("provenance metadata must be a dict")
        source_task_id = data.get("source_task_id")
        source_agent = data.get("source_agent")
        derivation = data.get("derivation", "direct_observation")
        recorded_at = data.get("recorded_at", 0.0)
        if not isinstance(source_task_id, str) or not source_task_id:
            raise ValueError("source_task_id must be a non-empty string")
        if not isinstance(source_agent, str) or not source_agent:
            raise ValueError("source_agent must be a non-empty string")
        if not isinstance(derivation, str) or not derivation:
            raise ValueError("derivation must be a non-empty string")
        if not isinstance(recorded_at, (int, float)):
            raise TypeError("recorded_at must be a number")
        return cls(
            source_task_id=source_task_id,
            source_agent=source_agent,
            evidence_memory_ids=list(evidence),
            derivation=derivation,
            recorded_at=float(recorded_at),
            metadata=dict(metadata),
        )


def build_provenance(
    source_task_id: str,
    source_agent: str,
    evidence_memory_ids: Iterable[str] = (),
    derivation: str = "direct_observation",
    recorded_at: float | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    return MemoryProvenance(
        source_task_id=source_task_id,
        source_agent=source_agent,
        evidence_memory_ids=list(evidence_memory_ids),
        derivation=derivation,
        recorded_at=time.time() if recorded_at is None else float(recorded_at),
        metadata=dict(metadata or {}),
    ).to_dict()
