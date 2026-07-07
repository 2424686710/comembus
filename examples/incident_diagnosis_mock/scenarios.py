"""Scenario set used by CoMemBus incident diagnosis experiments."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping


@dataclass(frozen=True)
class IncidentScenario:
    task_index: int
    task_topic: str
    family: str
    log_pattern: str
    config_issue: str
    expected_root_cause: str
    tags: List[str] = field(default_factory=list)
    related_memory_query: str = ""
    expected_skipped_steps: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_index": self.task_index,
            "task_topic": self.task_topic,
            "family": self.family,
            "log_pattern": self.log_pattern,
            "config_issue": self.config_issue,
            "expected_root_cause": self.expected_root_cause,
            "tags": list(self.tags),
            "related_memory_query": self.related_memory_query,
            "expected_skipped_steps": list(self.expected_skipped_steps),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IncidentScenario":
        tags = data.get("tags", [])
        expected_skipped_steps = data.get("expected_skipped_steps", [])
        if not isinstance(tags, list) or not all(isinstance(item, str) for item in tags):
            raise TypeError("tags must be a list of strings")
        if not isinstance(expected_skipped_steps, list) or not all(
            isinstance(item, str) for item in expected_skipped_steps
        ):
            raise TypeError("expected_skipped_steps must be a list of strings")
        task_index = data.get("task_index")
        if not isinstance(task_index, int):
            raise TypeError("task_index must be an integer")
        return cls(
            task_index=task_index,
            task_topic=_require_string(data, "task_topic"),
            family=_require_string(data, "family"),
            log_pattern=_require_string(data, "log_pattern"),
            config_issue=_require_string(data, "config_issue"),
            expected_root_cause=_require_string(data, "expected_root_cause"),
            tags=list(tags),
            related_memory_query=_require_string(data, "related_memory_query"),
            expected_skipped_steps=list(expected_skipped_steps),
        )

    def to_json_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


def default_scenarios() -> List[IncidentScenario]:
    scenarios: List[IncidentScenario] = []
    scenarios.extend(
        [
            _scenario(
                task_index=1,
                task_topic="checkout database timeout on primary failover",
                family="database_timeout",
                log_pattern="DatabaseTimeout during checkout connection setup",
                config_issue="database.port=15432 points to an obsolete replica",
                expected_root_cause="wrong database port caused database timeout",
                tags=["database", "timeout", "port"],
                related_memory_query="database timeout wrong port checkout",
                expected_skipped_steps=[],
            ),
            _scenario(
                task_index=2,
                task_topic="checkout timeout after config drift on blue deployment",
                family="database_timeout",
                log_pattern="DatabaseTimeout plus slow pool acquisition",
                config_issue="database.port=15432 still targets the wrong port",
                expected_root_cause="wrong database port caused database timeout",
                tags=["database", "timeout", "port"],
                related_memory_query="database timeout wrong port checkout",
                expected_skipped_steps=["full_log_scan", "deep_config_diff"],
            ),
            _scenario(
                task_index=3,
                task_topic="payment timeout while talking to checkout database",
                family="database_timeout",
                log_pattern="ConnectionPoolExhausted followed by DatabaseTimeout",
                config_issue="database.port=15432 mismatches the active cluster listener",
                expected_root_cause="wrong database port caused database timeout",
                tags=["database", "timeout", "port"],
                related_memory_query="database timeout wrong port checkout",
                expected_skipped_steps=["full_log_scan", "rebuild_dependency_map"],
            ),
            _scenario(
                task_index=4,
                task_topic="canary rollout triggers repeated postgres timeout",
                family="database_timeout",
                log_pattern="DatabaseTimeout repeated on canary checkout requests",
                config_issue="database.port=15432 remains pinned to a retired listener",
                expected_root_cause="wrong database port caused database timeout",
                tags=["database", "timeout", "port"],
                related_memory_query="database timeout wrong port checkout",
                expected_skipped_steps=["full_log_scan", "manual_port_inventory"],
            ),
        ]
    )
    scenarios.extend(
        [
            _scenario(
                task_index=5,
                task_topic="credential refresh fails with permission denied",
                family="permission_denied",
                log_pattern="PermissionDenied while opening database credentials file",
                config_issue="db.credentials_mode=0600 but owner=user mismatch blocks access",
                expected_root_cause="credential file permission denied blocked database access",
                tags=["permission", "database", "credentials"],
                related_memory_query="permission denied credentials database access",
                expected_skipped_steps=[],
            ),
            _scenario(
                task_index=6,
                task_topic="worker restart still cannot read secret volume",
                family="permission_denied",
                log_pattern="PermissionDenied on mounted credentials volume after restart",
                config_issue="credentials volume owner does not match runtime uid",
                expected_root_cause="credential file permission denied blocked database access",
                tags=["permission", "database", "credentials"],
                related_memory_query="permission denied credentials database access",
                expected_skipped_steps=["secret_volume_recheck"],
            ),
            _scenario(
                task_index=7,
                task_topic="batch node loses database credentials after hardening",
                family="permission_denied",
                log_pattern="PermissionDenied while reading db secrets after hardening",
                config_issue="credentials file owner/group mismatch denies read access",
                expected_root_cause="credential file permission denied blocked database access",
                tags=["permission", "database", "credentials"],
                related_memory_query="permission denied credentials database access",
                expected_skipped_steps=["filesystem_audit", "secret_volume_recheck"],
            ),
            _scenario(
                task_index=8,
                task_topic="checkout jobs fail to load credential bundle",
                family="permission_denied",
                log_pattern="PermissionDenied raised during credential bundle load",
                config_issue="credential bundle mount is readable only by the wrong service account",
                expected_root_cause="credential file permission denied blocked database access",
                tags=["permission", "database", "credentials"],
                related_memory_query="permission denied credentials database access",
                expected_skipped_steps=["full_log_scan", "secret_volume_recheck"],
            ),
        ]
    )
    scenarios.extend(
        [
            _scenario(
                task_index=9,
                task_topic="database writes fail because storage is full",
                family="storage_full",
                log_pattern="NoSpaceLeftOnDevice while writing WAL segment",
                config_issue="database.data_volume_usage=98% on primary disk",
                expected_root_cause="database storage volume full caused write failures",
                tags=["storage", "database", "disk"],
                related_memory_query="storage full wal no space database volume",
                expected_skipped_steps=[],
            ),
            _scenario(
                task_index=10,
                task_topic="replica promotion stalls on no space left error",
                family="storage_full",
                log_pattern="NoSpaceLeftOnDevice blocks replica WAL replay",
                config_issue="database.data_volume_usage=97% on replica disk",
                expected_root_cause="database storage volume full caused write failures",
                tags=["storage", "database", "disk"],
                related_memory_query="storage full wal no space database volume",
                expected_skipped_steps=["disk_inventory_scan"],
            ),
            _scenario(
                task_index=11,
                task_topic="maintenance job aborts because postgres volume is full",
                family="storage_full",
                log_pattern="NoSpaceLeftOnDevice during maintenance vacuum task",
                config_issue="database.data_volume_usage=99% after backlog growth",
                expected_root_cause="database storage volume full caused write failures",
                tags=["storage", "database", "disk"],
                related_memory_query="storage full wal no space database volume",
                expected_skipped_steps=["disk_inventory_scan", "deep_wal_trace"],
            ),
            _scenario(
                task_index=12,
                task_topic="checkout recovery loop hits disk-full condition again",
                family="storage_full",
                log_pattern="NoSpaceLeftOnDevice during checkout recovery checkpoint",
                config_issue="database.data_volume_usage=99% with stale archive retention",
                expected_root_cause="database storage volume full caused write failures",
                tags=["storage", "database", "disk"],
                related_memory_query="storage full wal no space database volume",
                expected_skipped_steps=["disk_inventory_scan", "archive_catalog_scan"],
            ),
        ]
    )
    return scenarios


def load_scenarios(path: str) -> List[IncidentScenario]:
    scenarios: List[IncidentScenario] = []
    scenario_path = Path(path)
    with scenario_path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid scenario JSON on line {line_number} in {path}"
                ) from exc
            scenarios.append(IncidentScenario.from_dict(payload))
    return scenarios


def expand_scenarios(
    scenarios: List[IncidentScenario],
    task_count: int,
) -> List[IncidentScenario]:
    if task_count <= 0:
        raise ValueError("task_count must be positive")
    if not scenarios:
        raise ValueError("at least one scenario is required")
    if task_count <= len(scenarios):
        return scenarios[:task_count]

    expanded: List[IncidentScenario] = []
    base_count = len(scenarios)
    for index in range(task_count):
        base = scenarios[index % base_count]
        cycle = (index // base_count) + 1
        task_index = index + 1
        task_topic = base.task_topic
        if cycle > 1:
            task_topic = f"{base.task_topic} [cycle {cycle}]"
        expanded.append(
            IncidentScenario(
                task_index=task_index,
                task_topic=task_topic,
                family=base.family,
                log_pattern=base.log_pattern,
                config_issue=base.config_issue,
                expected_root_cause=base.expected_root_cause,
                tags=list(base.tags),
                related_memory_query=base.related_memory_query,
                expected_skipped_steps=list(base.expected_skipped_steps),
            )
        )
    return expanded


def scenario_to_log_bytes(
    scenario: IncidentScenario,
    size_bytes: int = 8 * 1024 * 1024,
) -> bytes:
    if size_bytes <= 0:
        raise ValueError("size_bytes must be positive")

    lines = [
        f"INFO incident family={scenario.family} topic={scenario.task_topic}",
        f"WARN signal pattern={scenario.log_pattern}",
        f"ERROR expected_root_cause={scenario.expected_root_cause}",
        f"INFO tags={','.join(scenario.tags)}",
    ]
    block = ("\n".join(lines) + "\n").encode("utf-8")
    repeat_count = (size_bytes // len(block)) + 1
    return (block * repeat_count)[:size_bytes]


def scenario_to_config_text(scenario: IncidentScenario) -> str:
    return "\n".join(
        [
            "service.name=checkout-api",
            f"incident.family={scenario.family}",
            f"task.topic={scenario.task_topic}",
            f"config.issue={scenario.config_issue}",
            f"expected.root_cause={scenario.expected_root_cause}",
            f"related.memory_query={scenario.related_memory_query}",
        ]
    )


def _scenario(**kwargs: Any) -> IncidentScenario:
    return IncidentScenario(**kwargs)


def _require_string(data: Mapping[str, Any], field_name: str) -> str:
    value = data.get(field_name)
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    return value
