"""Structured task state for multi-agent handoff."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from typing import Any, Dict, List, Mapping


@dataclass
class TaskState:
    """A versioned task snapshot shared across multiple agents."""

    task_id: str
    version: int
    goal: str
    phase: str
    completed_steps: List[str] = field(default_factory=list)
    pending_steps: List[str] = field(default_factory=list)
    facts: Dict[str, str] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    artifacts: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "version": self.version,
            "goal": self.goal,
            "phase": self.phase,
            "completed_steps": list(self.completed_steps),
            "pending_steps": list(self.pending_steps),
            "facts": dict(self.facts),
            "errors": list(self.errors),
            "artifacts": deepcopy(self.artifacts),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TaskState":
        if not isinstance(data, Mapping):
            raise TypeError("task state must be a mapping")

        task_id = _require_string(data, "task_id")
        version = _require_int(data, "version")
        goal = _require_string(data, "goal")
        phase = _require_string(data, "phase")
        completed_steps = _require_string_list(data, "completed_steps")
        pending_steps = _require_string_list(data, "pending_steps")
        facts = _require_string_dict(data, "facts")
        errors = _require_string_list(data, "errors")
        artifacts = _require_artifacts_dict(data, "artifacts")

        return cls(
            task_id=task_id,
            version=version,
            goal=goal,
            phase=phase,
            completed_steps=completed_steps,
            pending_steps=pending_steps,
            facts=facts,
            errors=errors,
            artifacts=artifacts,
        )

    def to_json_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


def _require_string(data: Mapping[str, Any], field_name: str) -> str:
    value = data.get(field_name)
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    return value


def _require_int(data: Mapping[str, Any], field_name: str) -> int:
    value = data.get(field_name)
    if not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value


def _require_string_list(data: Mapping[str, Any], field_name: str) -> List[str]:
    value = data.get(field_name)
    if not isinstance(value, list):
        raise TypeError(f"{field_name} must be a list")
    if not all(isinstance(item, str) for item in value):
        raise TypeError(f"{field_name} must only contain strings")
    return list(value)


def _require_string_dict(data: Mapping[str, Any], field_name: str) -> Dict[str, str]:
    value = data.get(field_name)
    if not isinstance(value, dict):
        raise TypeError(f"{field_name} must be a dict")
    result: Dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise TypeError(f"{field_name} must map strings to strings")
        result[key] = item
    return result


def _require_artifacts_dict(
    data: Mapping[str, Any],
    field_name: str,
) -> Dict[str, Dict[str, Any]]:
    value = data.get(field_name)
    if not isinstance(value, dict):
        raise TypeError(f"{field_name} must be a dict")
    artifacts: Dict[str, Dict[str, Any]] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(f"{field_name} keys must be strings")
        if not isinstance(item, dict):
            raise TypeError(f"{field_name} values must be dict objects")
        artifacts[key] = deepcopy(item)
    return artifacts

