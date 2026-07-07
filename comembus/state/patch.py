"""Incremental task state patches."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from typing import Any, Dict, Mapping

from .task_state import TaskState

LIST_FIELDS = {"completed_steps", "pending_steps", "errors"}
DICT_FIELDS = {"facts", "artifacts"}
IMMUTABLE_FIELDS = {"task_id", "version"}
TASK_STATE_FIELDS = {
    "task_id",
    "version",
    "goal",
    "phase",
    "completed_steps",
    "pending_steps",
    "facts",
    "errors",
    "artifacts",
}


class StatePatchError(Exception):
    """Base state patch error."""


class VersionConflictError(StatePatchError):
    """Raised when a patch is applied to the wrong state version."""


@dataclass
class StatePatch:
    """A compact state delta to apply on top of a known version."""

    task_id: str
    expected_version: int
    set_fields: Dict[str, Any] = field(default_factory=dict)
    append_fields: Dict[str, list[str]] = field(default_factory=dict)
    merge_dict_fields: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "expected_version": self.expected_version,
            "set_fields": deepcopy(self.set_fields),
            "append_fields": deepcopy(self.append_fields),
            "merge_dict_fields": deepcopy(self.merge_dict_fields),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StatePatch":
        if not isinstance(data, Mapping):
            raise TypeError("state patch must be a mapping")

        task_id = data.get("task_id")
        expected_version = data.get("expected_version")
        set_fields = data.get("set_fields", {})
        append_fields = data.get("append_fields", {})
        merge_dict_fields = data.get("merge_dict_fields", {})

        if not isinstance(task_id, str):
            raise TypeError("task_id must be a string")
        if not isinstance(expected_version, int):
            raise TypeError("expected_version must be an integer")
        if expected_version < 0:
            raise ValueError("expected_version must be non-negative")
        if not isinstance(set_fields, dict):
            raise TypeError("set_fields must be a dict")
        if not isinstance(append_fields, dict):
            raise TypeError("append_fields must be a dict")
        if not isinstance(merge_dict_fields, dict):
            raise TypeError("merge_dict_fields must be a dict")

        return cls(
            task_id=task_id,
            expected_version=expected_version,
            set_fields=deepcopy(set_fields),
            append_fields=_validate_append_fields(append_fields),
            merge_dict_fields=_validate_merge_dict_fields(merge_dict_fields),
        )

    def to_json_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


def apply_patch(state: TaskState, patch: StatePatch) -> TaskState:
    if state.task_id != patch.task_id:
        raise StatePatchError("patch task_id does not match state task_id")
    if patch.expected_version != state.version:
        raise VersionConflictError(
            f"expected version {patch.expected_version}, found {state.version}"
        )

    updated = state.to_dict()
    _apply_set_fields(updated, patch.set_fields)
    _apply_append_fields(updated, patch.append_fields)
    _apply_merge_dict_fields(updated, patch.merge_dict_fields)
    updated["version"] = state.version + 1
    return TaskState.from_dict(updated)


def _apply_set_fields(state_dict: Dict[str, Any], set_fields: Dict[str, Any]) -> None:
    for field_name, value in set_fields.items():
        _validate_mutable_field_name(field_name)
        state_dict[field_name] = deepcopy(value)


def _apply_append_fields(
    state_dict: Dict[str, Any],
    append_fields: Dict[str, list[str]],
) -> None:
    for field_name, values in append_fields.items():
        _validate_mutable_field_name(field_name)
        if field_name not in LIST_FIELDS:
            raise StatePatchError(f"append_fields only supports list fields: {field_name}")
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise TypeError(f"append_fields[{field_name}] must be a list of strings")
        current = state_dict.get(field_name)
        if not isinstance(current, list):
            raise StatePatchError(f"state field is not a list: {field_name}")
        current.extend(values)


def _apply_merge_dict_fields(
    state_dict: Dict[str, Any],
    merge_dict_fields: Dict[str, Dict[str, Any]],
) -> None:
    for field_name, values in merge_dict_fields.items():
        _validate_mutable_field_name(field_name)
        if field_name not in DICT_FIELDS:
            raise StatePatchError(
                f"merge_dict_fields only supports dict fields: {field_name}"
            )
        if not isinstance(values, dict):
            raise TypeError(f"merge_dict_fields[{field_name}] must be a dict")
        current = state_dict.get(field_name)
        if not isinstance(current, dict):
            raise StatePatchError(f"state field is not a dict: {field_name}")
        if field_name == "facts":
            for key, item in values.items():
                if not isinstance(key, str) or not isinstance(item, str):
                    raise TypeError("facts merges must map strings to strings")
        elif field_name == "artifacts":
            for key, item in values.items():
                if not isinstance(key, str) or not isinstance(item, dict):
                    raise TypeError("artifacts merges must map strings to dict objects")
        current.update(deepcopy(values))


def _validate_mutable_field_name(field_name: str) -> None:
    if field_name not in TASK_STATE_FIELDS:
        raise StatePatchError(f"unknown task state field: {field_name}")
    if field_name in IMMUTABLE_FIELDS:
        raise StatePatchError(f"field cannot be modified via patch: {field_name}")


def _validate_append_fields(values: Dict[str, Any]) -> Dict[str, list[str]]:
    result: Dict[str, list[str]] = {}
    for field_name, field_value in values.items():
        if not isinstance(field_name, str):
            raise TypeError("append_fields keys must be strings")
        if not isinstance(field_value, list) or not all(
            isinstance(item, str) for item in field_value
        ):
            raise TypeError("append_fields values must be lists of strings")
        result[field_name] = list(field_value)
    return result


def _validate_merge_dict_fields(values: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for field_name, field_value in values.items():
        if not isinstance(field_name, str):
            raise TypeError("merge_dict_fields keys must be strings")
        if not isinstance(field_value, dict):
            raise TypeError("merge_dict_fields values must be dict objects")
        result[field_name] = deepcopy(field_value)
    return result

