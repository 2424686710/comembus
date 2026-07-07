"""Protocol dataclasses for collaboration mode experiments."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from typing import Any, Dict, List, Mapping, Optional


@dataclass
class AgentCapability:
    """Describe what an agent can do in structured mode."""

    agent_id: str
    role: str
    actions: List[str] = field(default_factory=list)
    input_types: List[str] = field(default_factory=list)
    output_types: List[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "actions": list(self.actions),
            "input_types": list(self.input_types),
            "output_types": list(self.output_types),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgentCapability":
        return cls(
            agent_id=_require_string(data, "agent_id"),
            role=_require_string(data, "role"),
            actions=_require_string_list(data, "actions"),
            input_types=_require_string_list(data, "input_types"),
            output_types=_require_string_list(data, "output_types"),
            description=_require_string(data, "description"),
        )

    def to_json_bytes(self) -> bytes:
        return _to_json_bytes(self.to_dict())


@dataclass
class StructuredMessage:
    """A structured collaboration message."""

    message_id: str
    task_id: str
    source_agent: str
    target_agent: str
    action_type: str
    params: Dict[str, Any] = field(default_factory=dict)
    result: Dict[str, Any] = field(default_factory=dict)
    capability: Optional[Dict[str, Any]] = None
    object_refs: List[Dict[str, Any]] = field(default_factory=list)
    state_patch: Optional[Dict[str, Any]] = None
    memory_refs: List[str] = field(default_factory=list)
    created_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "task_id": self.task_id,
            "source_agent": self.source_agent,
            "target_agent": self.target_agent,
            "action_type": self.action_type,
            "params": deepcopy(self.params),
            "result": deepcopy(self.result),
            "capability": deepcopy(self.capability),
            "object_refs": deepcopy(self.object_refs),
            "state_patch": deepcopy(self.state_patch),
            "memory_refs": list(self.memory_refs),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StructuredMessage":
        capability = data.get("capability")
        if capability is not None and not isinstance(capability, dict):
            raise TypeError("capability must be a dict or null")
        object_refs = data.get("object_refs", [])
        state_patch = data.get("state_patch")
        memory_refs = data.get("memory_refs", [])
        params = data.get("params", {})
        result = data.get("result", {})

        if not isinstance(params, dict):
            raise TypeError("params must be a dict")
        if not isinstance(result, dict):
            raise TypeError("result must be a dict")
        if not isinstance(object_refs, list) or not all(
            isinstance(item, dict) for item in object_refs
        ):
            raise TypeError("object_refs must be a list of dicts")
        if state_patch is not None and not isinstance(state_patch, dict):
            raise TypeError("state_patch must be a dict or null")
        if not isinstance(memory_refs, list) or not all(
            isinstance(item, str) for item in memory_refs
        ):
            raise TypeError("memory_refs must be a list of strings")

        return cls(
            message_id=_require_string(data, "message_id"),
            task_id=_require_string(data, "task_id"),
            source_agent=_require_string(data, "source_agent"),
            target_agent=_require_string(data, "target_agent"),
            action_type=_require_string(data, "action_type"),
            params=deepcopy(params),
            result=deepcopy(result),
            capability=deepcopy(capability),
            object_refs=deepcopy(object_refs),
            state_patch=deepcopy(state_patch),
            memory_refs=list(memory_refs),
            created_at=_require_float(data, "created_at"),
        )

    def to_json_bytes(self) -> bytes:
        return _to_json_bytes(self.to_dict())


@dataclass
class TextMessage:
    """A text-heavy collaboration message."""

    message_id: str
    task_id: str
    source_agent: str
    target_agent: str
    text: str
    created_at: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "task_id": self.task_id,
            "source_agent": self.source_agent,
            "target_agent": self.target_agent,
            "text": self.text,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TextMessage":
        return cls(
            message_id=_require_string(data, "message_id"),
            task_id=_require_string(data, "task_id"),
            source_agent=_require_string(data, "source_agent"),
            target_agent=_require_string(data, "target_agent"),
            text=_require_string(data, "text"),
            created_at=_require_float(data, "created_at"),
        )

    def to_json_bytes(self) -> bytes:
        return _to_json_bytes(self.to_dict())


def _to_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _require_string(data: Mapping[str, Any], field_name: str) -> str:
    value = data.get(field_name)
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    return value


def _require_string_list(data: Mapping[str, Any], field_name: str) -> List[str]:
    value = data.get(field_name)
    if not isinstance(value, list):
        raise TypeError(f"{field_name} must be a list")
    if not all(isinstance(item, str) for item in value):
        raise TypeError(f"{field_name} must only contain strings")
    return list(value)


def _require_float(data: Mapping[str, Any], field_name: str) -> float:
    value = data.get(field_name)
    if not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    return float(value)

