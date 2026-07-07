"""Metrics helpers for collaboration mode experiments."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Dict, Mapping


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return int(math.ceil(len(text) / 4.0))


def count_text_chars(payload: object) -> int:
    if payload is None:
        return 0
    if isinstance(payload, str):
        return len(payload)
    if isinstance(payload, Mapping):
        return sum(count_text_chars(value) for value in payload.values())
    if isinstance(payload, (list, tuple, set)):
        return sum(count_text_chars(value) for value in payload)
    return 0


def json_size_bytes(obj: object) -> int:
    return len(json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8"))


@dataclass(frozen=True)
class CollaborationMetrics:
    """Comparable metrics for one collaboration run."""

    mode: str
    task_index: int
    task_topic: str
    message_count: int
    text_chars: int
    approx_tokens: int
    protocol_bytes: int
    object_ref_count: int
    state_patch_count: int
    memory_ref_count: int
    non_text_state_bytes: int
    shared_object_bytes: int
    memory_hit: bool
    reused_memory_id: str
    baseline_steps: int
    actual_steps: int
    saved_steps: int
    total_latency_ms: float
    root_cause_correct: bool
    scenario_family: str
    capability_count: int
    capability_discovery_count: int
    embedding_state_count: int
    embedding_state_bytes: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "task_index": self.task_index,
            "task_topic": self.task_topic,
            "message_count": self.message_count,
            "text_chars": self.text_chars,
            "approx_tokens": self.approx_tokens,
            "protocol_bytes": self.protocol_bytes,
            "object_ref_count": self.object_ref_count,
            "state_patch_count": self.state_patch_count,
            "memory_ref_count": self.memory_ref_count,
            "non_text_state_bytes": self.non_text_state_bytes,
            "shared_object_bytes": self.shared_object_bytes,
            "memory_hit": self.memory_hit,
            "reused_memory_id": self.reused_memory_id,
            "baseline_steps": self.baseline_steps,
            "actual_steps": self.actual_steps,
            "saved_steps": self.saved_steps,
            "total_latency_ms": self.total_latency_ms,
            "root_cause_correct": self.root_cause_correct,
            "scenario_family": self.scenario_family,
            "capability_count": self.capability_count,
            "capability_discovery_count": self.capability_discovery_count,
            "embedding_state_count": self.embedding_state_count,
            "embedding_state_bytes": self.embedding_state_bytes,
        }
