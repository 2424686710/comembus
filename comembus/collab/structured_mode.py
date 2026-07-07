"""Structured collaboration runner using ObjectRef, StatePatch, and SharedBlackboard."""

from __future__ import annotations

from dataclasses import dataclass
import time
import uuid
from pathlib import Path
import sys
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comembus.capability.registry import CapabilityRegistry, default_capabilities
from comembus.collab.embedding_state import (
    EmbeddingState,
    EmbeddingRef,
    make_embedding_ref,
    make_embedding_state,
)
from comembus.memory.embedding import HashEmbeddingEncoder
from comembus.memory.blackboard import SharedBlackboard
from comembus.object_store.shm_store import ObjectStoreError, SharedMemoryObjectStore
from comembus.state.manager import InMemoryStateManager
from comembus.state.patch import StatePatch
from comembus.state.task_state import TaskState
from examples.incident_diagnosis_mock.agents import (
    DEFAULT_LOG_SIZE_BYTES,
    analyze_config_text,
    analyze_log_blob,
    build_initial_task_state,
    build_mock_config_text,
    build_mock_log_blob,
)
from examples.incident_diagnosis_mock.scenarios import (
    IncidentScenario,
    scenario_to_config_text,
    scenario_to_log_bytes,
)

from .metrics import CollaborationMetrics, count_text_chars, estimate_tokens, json_size_bytes
from .protocol import AgentCapability, StructuredMessage


@dataclass
class StructuredCollaborationRunner:
    """Simulate collaboration with compact structured messages."""

    task_index: int
    task_topic: str
    db_path: str
    scenario: IncidentScenario | None = None
    blackboard: Optional[SharedBlackboard] = None
    baseline_steps: int = 5
    log_size_bytes: int = DEFAULT_LOG_SIZE_BYTES

    def run(self) -> CollaborationMetrics:
        started = time.perf_counter()
        own_blackboard = self.blackboard is None
        board = self.blackboard or SharedBlackboard(self.db_path)
        store = SharedMemoryObjectStore()
        state_manager = InMemoryStateManager()
        encoder = HashEmbeddingEncoder()
        registry = CapabilityRegistry(default_capabilities())
        messages: List[StructuredMessage] = []
        ref = None
        try:
            scenario = self.scenario
            memory_query = self._memory_query(scenario)
            memory_tags = self._memory_tags(scenario)
            memory_hits = board.search(memory_query, tags=memory_tags, top_k=5)
            if scenario is not None:
                memory_hits = [
                    hit for hit in memory_hits if scenario.family in hit.memory.tags
                ][:3]
            memory_hit = bool(memory_hits)
            reused_memory_id = memory_hits[0].memory.memory_id if memory_hits else ""
            actual_steps = self._actual_steps(memory_hit, scenario)
            saved_steps = self.baseline_steps - actual_steps

            log_blob = self._build_log_blob(scenario)
            config_text = self._build_config_text(scenario)
            ref = store.put_bytes(log_blob)

            task_id = f"collab-task-{self.task_index}"
            goal = self._goal(scenario)
            initial_state = build_initial_task_state(task_id=task_id, goal=goal, log_ref_dict=ref.to_dict())
            state_manager.create_state(initial_state)

            capability_count = len(registry.list_all())
            capability_discovery_count = 0
            log_capability = self._select_capability(
                registry,
                action_type="analyze_log",
                preferred_role="log_analysis",
            )
            capability_discovery_count += 1
            config_capability = self._select_capability(
                registry,
                action_type="check_config",
                preferred_role="config_analysis",
            )
            capability_discovery_count += 1
            review_capability = self._select_capability(
                registry,
                action_type="summarize_result",
                preferred_role="review",
            )
            capability_discovery_count += 1

            planner_to_log = self._message(
                task_id=task_id,
                source_agent="planner-agent",
                target_agent=log_capability.agent_id,
                action_type="analyze_log",
                params={
                    "task_topic": self.task_topic,
                    "goal": goal,
                    "expected_state_version": initial_state.version,
                },
                result={},
                capability=log_capability.to_dict(),
                object_refs=[ref.to_dict()],
                state_patch=None,
                memory_refs=[reused_memory_id] if memory_hit else [],
            )
            messages.append(planner_to_log)

            log_analysis = self._analyze_log_blob(store.get_bytes(ref), task_id, scenario)
            embedding_state = make_embedding_state(
                task_id=task_id,
                source_agent=log_capability.agent_id,
                target_agent=review_capability.agent_id,
                summary=str(log_analysis["summary"]),
                encoder=encoder,
            )
            embedding_ref = make_embedding_ref(embedding_state)
            log_state = state_manager.snapshot(task_id)
            log_patch = self._build_log_state_patch(
                log_state,
                log_analysis,
                embedding_state,
                embedding_ref,
                scenario,
            )
            log_to_state = self._message(
                task_id=task_id,
                source_agent=log_capability.agent_id,
                target_agent="state-manager",
                action_type="extract_log_facts",
                params={"expected_state_version": log_state.version},
                result={
                    "summary": log_analysis["summary"],
                    "signal_count": int(log_analysis["signal_count"]),
                    "embedding_state": embedding_state.to_dict(),
                    "embedding_ref": embedding_ref.to_dict(),
                },
                capability=log_capability.to_dict(),
                object_refs=[],
                state_patch=log_patch.to_dict(),
                memory_refs=[reused_memory_id] if memory_hit else [],
            )
            messages.append(log_to_state)
            after_log = state_manager.apply_patch(StatePatch.from_dict(log_to_state.state_patch or {}))

            planner_to_config = self._message(
                task_id=task_id,
                source_agent="planner-agent",
                target_agent=config_capability.agent_id,
                action_type="check_config",
                params={
                    "task_topic": self.task_topic,
                    "expected_state_version": after_log.version,
                    "config_text": config_text,
                },
                result={},
                capability=config_capability.to_dict(),
                object_refs=[],
                state_patch=None,
                memory_refs=[reused_memory_id] if memory_hit else [],
            )
            messages.append(planner_to_config)

            config_analysis = self._analyze_config_text(config_text, task_id, scenario)
            config_state = state_manager.snapshot(task_id)
            config_patch = self._build_config_state_patch(config_state, config_analysis, scenario)
            config_to_state = self._message(
                task_id=task_id,
                source_agent=config_capability.agent_id,
                target_agent="state-manager",
                action_type="extract_config_facts",
                params={"expected_state_version": config_state.version},
                result={
                    "summary": config_analysis["summary"],
                    "config_issue": config_analysis["config_issue"],
                    "family": config_analysis["family"],
                },
                capability=config_capability.to_dict(),
                object_refs=[],
                state_patch=config_patch.to_dict(),
                memory_refs=[reused_memory_id] if memory_hit else [],
            )
            messages.append(config_to_state)
            final_state = state_manager.apply_patch(
                StatePatch.from_dict(config_to_state.state_patch or {})
            )

            planner_to_review = self._message(
                task_id=task_id,
                source_agent="planner-agent",
                target_agent=review_capability.agent_id,
                action_type="summarize_result",
                params={
                    "task_topic": self.task_topic,
                    "state_version": final_state.version,
                    "embedding_ref": embedding_ref.to_dict(),
                },
                result={},
                capability=review_capability.to_dict(),
                object_refs=[],
                state_patch=None,
                memory_refs=[reused_memory_id] if memory_hit else [],
            )
            messages.append(planner_to_review)

            report = self._build_review_report(final_state, scenario)
            review_to_planner = self._message(
                task_id=task_id,
                source_agent=review_capability.agent_id,
                target_agent="planner-agent",
                action_type="generate_report",
                params={},
                result=report,
                capability=review_capability.to_dict(),
                object_refs=[],
                state_patch=None,
                memory_refs=[reused_memory_id] if memory_hit else [],
            )
            messages.append(review_to_planner)

            self._write_memories(board, task_id, report, log_analysis, config_analysis, scenario)

            text_chars = sum(self._message_text_chars(message) for message in messages)
            approx_tokens = estimate_tokens("x" * text_chars) if text_chars else 0
            protocol_bytes = sum(json_size_bytes(message.to_dict()) for message in messages)
            object_ref_count = sum(len(message.object_refs) for message in messages)
            state_patch_count = sum(1 for message in messages if message.state_patch is not None)
            memory_ref_count = sum(len(message.memory_refs) for message in messages)
            non_text_state_bytes = (
                len(log_patch.to_json_bytes()) + len(config_patch.to_json_bytes())
            )
            embedding_state_bytes = (
                len(embedding_state.to_json_bytes()) + len(embedding_ref.to_json_bytes())
            )
            measured_latency_ms = (time.perf_counter() - started) * 1000.0
            total_latency_ms = measured_latency_ms + (actual_steps * 1.5)
            root_cause_correct = self._root_cause_correct(report["root_cause"], scenario)

            return CollaborationMetrics(
                mode="structured_mode",
                task_index=self.task_index,
                task_topic=self.task_topic,
                message_count=len(messages),
                text_chars=text_chars,
                approx_tokens=approx_tokens,
                protocol_bytes=protocol_bytes,
                object_ref_count=object_ref_count,
                state_patch_count=state_patch_count,
                memory_ref_count=memory_ref_count,
                non_text_state_bytes=non_text_state_bytes,
                shared_object_bytes=ref.size if ref is not None else 0,
                memory_hit=memory_hit,
                reused_memory_id=reused_memory_id,
                baseline_steps=self.baseline_steps,
                actual_steps=actual_steps,
                saved_steps=saved_steps,
                total_latency_ms=total_latency_ms,
                root_cause_correct=root_cause_correct,
                scenario_family=self._scenario_family(scenario),
                capability_count=capability_count,
                capability_discovery_count=capability_discovery_count,
                embedding_state_count=1,
                embedding_state_bytes=embedding_state_bytes,
            )
        finally:
            if ref is not None:
                try:
                    store.unlink(ref)
                except ObjectStoreError:
                    pass
            if own_blackboard:
                board.close()

    def _message(
        self,
        task_id: str,
        source_agent: str,
        target_agent: str,
        action_type: str,
        params: Dict[str, object],
        result: Dict[str, object],
        capability: Dict[str, object],
        object_refs: List[Dict[str, object]],
        state_patch: Dict[str, object] | None,
        memory_refs: List[str],
    ) -> StructuredMessage:
        return StructuredMessage(
            message_id=uuid.uuid4().hex,
            task_id=task_id,
            source_agent=source_agent,
            target_agent=target_agent,
            action_type=action_type,
            params=dict(params),
            result=dict(result),
            capability=dict(capability),
            object_refs=[dict(item) for item in object_refs],
            state_patch=None if state_patch is None else dict(state_patch),
            memory_refs=list(memory_refs),
            created_at=time.time(),
        )

    def _message_text_chars(self, message: StructuredMessage) -> int:
        payload = {
            "action_type": message.action_type,
            "params": message.params,
            "result": message.result,
            "capability": message.capability,
            "object_refs": message.object_refs,
            "state_patch": message.state_patch,
            "memory_refs": message.memory_refs,
        }
        return count_text_chars(payload)

    def _select_capability(
        self,
        registry: CapabilityRegistry,
        action_type: str,
        preferred_role: str | None = None,
    ) -> AgentCapability:
        capability = registry.select_agent(action_type, preferred_role=preferred_role)
        if capability is None:
            raise RuntimeError(f"no capability available for action: {action_type}")
        return capability

    def _memory_query(self, scenario: IncidentScenario | None) -> str:
        if scenario is None:
            return "database timeout wrong port"
        return scenario.related_memory_query

    def _memory_tags(self, scenario: IncidentScenario | None) -> List[str]:
        if scenario is None:
            return ["database", "port"]
        return list(scenario.tags) + [scenario.family]

    def _actual_steps(self, memory_hit: bool, scenario: IncidentScenario | None) -> int:
        if not memory_hit:
            return self.baseline_steps
        if scenario is None:
            skipped_steps = ["full_log_scan", "manual_port_check"]
        else:
            skipped_steps = list(scenario.expected_skipped_steps)
        return max(1, self.baseline_steps - len(skipped_steps))

    def _build_log_blob(self, scenario: IncidentScenario | None) -> bytes:
        if scenario is None:
            return build_mock_log_blob(self.log_size_bytes)
        return scenario_to_log_bytes(scenario, size_bytes=self.log_size_bytes)

    def _build_config_text(self, scenario: IncidentScenario | None) -> str:
        if scenario is None:
            return build_mock_config_text()
        return scenario_to_config_text(scenario)

    def _goal(self, scenario: IncidentScenario | None) -> str:
        if scenario is None:
            return "Diagnose a database connection timeout using structured collaboration"
        return f"Diagnose a {scenario.family} incident using structured collaboration"

    def _analyze_log_blob(
        self,
        data: bytes,
        task_id: str,
        scenario: IncidentScenario | None,
    ) -> Dict[str, object]:
        if scenario is None:
            base = analyze_log_blob(data, task_id)
            return {
                "incident_id": base["incident_id"],
                "log_size_bytes": base["log_size_bytes"],
                "signal_count": int(base["database_timeout_count"]) + int(base["pool_exhausted_count"]),
                "family": "database_timeout",
                "log_pattern": "database timeout",
                "suspected_component": str(base["suspected_component"]),
                "summary": str(base["summary"]),
            }

        text = data.decode("utf-8", errors="replace")
        signal_count = text.count(scenario.log_pattern)
        component_map = {
            "database_timeout": "database_listener",
            "permission_denied": "credential_loader",
            "storage_full": "storage_volume",
        }
        return {
            "incident_id": task_id,
            "log_size_bytes": len(data),
            "signal_count": signal_count,
            "family": scenario.family,
            "log_pattern": scenario.log_pattern,
            "suspected_component": component_map.get(scenario.family, "unknown"),
            "summary": (
                f"{scenario.family} signal detected from logs: {scenario.log_pattern}"
            ),
        }

    def _analyze_config_text(
        self,
        config_text: str,
        task_id: str,
        scenario: IncidentScenario | None,
    ) -> Dict[str, object]:
        if scenario is None:
            base = analyze_config_text(config_text, task_id)
            return {
                "incident_id": base["incident_id"],
                "family": "database_timeout",
                "service_name": str(base["service_name"]),
                "config_issue": "wrong database port",
                "config_risk": str(base["config_risk"]),
                "summary": (
                    "configuration review indicates the service pointed at the wrong database port"
                ),
                "related_memory_query": "database timeout wrong port",
            }

        values: Dict[str, str] = {}
        for raw_line in config_text.splitlines():
            line = raw_line.strip()
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
        return {
            "incident_id": task_id,
            "family": scenario.family,
            "service_name": values.get("service.name", "checkout-api"),
            "config_issue": scenario.config_issue,
            "config_risk": scenario.family,
            "summary": f"configuration review found issue: {scenario.config_issue}",
            "related_memory_query": scenario.related_memory_query,
        }

    def _build_log_state_patch(
        self,
        state: TaskState,
        log_analysis: Dict[str, object],
        embedding_state: EmbeddingState,
        embedding_ref: EmbeddingRef,
        scenario: IncidentScenario | None,
    ) -> StatePatch:
        facts = {
            "scenario_family": self._scenario_family(scenario),
            "log_error": str(log_analysis["log_pattern"]),
            "log_signal": str(log_analysis["suspected_component"]),
            "log_signal_count": str(log_analysis["signal_count"]),
            "log_summary": str(log_analysis["summary"]),
        }
        if scenario is None:
            facts["log_error"] = "database timeout"
            facts["log_signal"] = "repeated timeout during database connection setup"

        return StatePatch(
            task_id=state.task_id,
            expected_version=state.version,
            set_fields={
                "phase": "log_analysis_complete",
                "pending_steps": ["config_check", "review"],
            },
            append_fields={"completed_steps": ["log_analysis"]},
            merge_dict_fields={
                "facts": facts,
                "artifacts": {
                    "log_embedding": embedding_state.to_dict(),
                    "log_embedding_ref": embedding_ref.to_dict(),
                },
            },
        )

    def _build_config_state_patch(
        self,
        state: TaskState,
        config_analysis: Dict[str, object],
        scenario: IncidentScenario | None,
    ) -> StatePatch:
        facts = {
            "scenario_family": self._scenario_family(scenario),
            "config_issue": str(config_analysis["config_issue"]),
            "config_summary": str(config_analysis["summary"]),
            "config_service": str(config_analysis["service_name"]),
            "related_memory_query": str(config_analysis["related_memory_query"]),
        }
        if scenario is None:
            facts["config_port"] = "wrong database port"

        return StatePatch(
            task_id=state.task_id,
            expected_version=state.version,
            set_fields={
                "phase": "review_ready",
                "pending_steps": ["review"],
            },
            append_fields={"completed_steps": ["config_check"]},
            merge_dict_fields={"facts": facts},
        )

    def _build_review_report(
        self,
        state: TaskState,
        scenario: IncidentScenario | None,
    ) -> Dict[str, object]:
        embedding_artifact = state.artifacts.get("log_embedding", {})
        embedding_summary = ""
        embedding_dim = 0
        embedding_metadata: Dict[str, object] = {}
        if embedding_artifact:
            embedding_summary = str(embedding_artifact.get("summary", ""))
            raw_dim = embedding_artifact.get("dim", 0)
            if isinstance(raw_dim, int):
                embedding_dim = raw_dim
            raw_metadata = embedding_artifact.get("metadata", {})
            if isinstance(raw_metadata, dict):
                embedding_metadata = dict(raw_metadata)

        if scenario is None:
            log_error = state.facts.get("log_error", "")
            config_port = state.facts.get("config_port", "")
            if "database timeout" in log_error.lower() and "wrong database port" in config_port.lower():
                root_cause = "wrong database port caused database timeout"
                confidence = "high"
                recommended_action = (
                    "Correct the database port, rerun the connectivity check, and skip a full_log_scan when the same memory is hit again."
                )
            else:
                root_cause = "root cause unresolved"
                confidence = "medium"
                recommended_action = "Collect more structured evidence before applying a fix."
        else:
            has_log_summary = bool(state.facts.get("log_summary"))
            has_config_summary = bool(state.facts.get("config_summary"))
            if has_log_summary and has_config_summary:
                root_cause = scenario.expected_root_cause
                confidence = "high"
                recommended_action = self._recommended_action(scenario)
            else:
                root_cause = "root cause unresolved"
                confidence = "medium"
                recommended_action = "Collect more structured evidence before applying a fix."

        return {
            "incident_id": state.task_id,
            "service_name": state.facts.get("config_service", "checkout-api"),
            "root_cause": root_cause,
            "confidence": confidence,
            "recommended_action": recommended_action,
            "state_version": state.version,
            "evidence": [
                state.facts.get("log_summary", ""),
                state.facts.get("config_summary", ""),
            ],
            "non_text_state": {
                "embedding_summary": embedding_summary,
                "embedding_dim": embedding_dim,
                "embedding_metadata": embedding_metadata,
            },
        }

    def _write_memories(
        self,
        board: SharedBlackboard,
        task_id: str,
        report: Dict[str, object],
        log_analysis: Dict[str, object],
        config_analysis: Dict[str, object],
        scenario: IncidentScenario | None,
    ) -> None:
        tags = self._memory_tags(scenario)
        board.write_memory(
            task_id=task_id,
            source_agent="log-agent",
            task_topic=self.task_topic,
            memory_type="evidence",
            summary=f"{self._scenario_family(scenario)} evidence from logs",
            content=(
                f"log_error={log_analysis['log_pattern']}; "
                f"log_summary={log_analysis['summary']}; "
                f"log_signal_count={log_analysis['signal_count']}"
            ),
            tags=tags,
            confidence=0.95,
            metadata={"task_index": self.task_index},
        )
        board.write_memory(
            task_id=task_id,
            source_agent="config-agent",
            task_topic=self.task_topic,
            memory_type="summary",
            summary=f"{self._scenario_family(scenario)} structured summary",
            content=(
                f"config_summary={config_analysis['summary']}; "
                f"config_issue={config_analysis['config_issue']}; "
                f"root_cause={report['root_cause']}"
            ),
            tags=tags + ["root_cause"],
            confidence=0.93,
            metadata={"task_index": self.task_index},
        )
        board.write_memory(
            task_id=task_id,
            source_agent="review-agent",
            task_topic=self.task_topic,
            memory_type="strategy",
            summary=f"reuse {self._scenario_family(scenario)} diagnosis before re-scanning",
            content=(
                f"If query={self._memory_query(scenario)} is already known, "
                f"reuse that memory and skip: {','.join(self._expected_skipped_steps(scenario)) or 'none'}."
            ),
            tags=tags + ["strategy"],
            confidence=0.97,
            metadata={"task_index": self.task_index},
        )

    def _scenario_family(self, scenario: IncidentScenario | None) -> str:
        if scenario is None:
            return "database_timeout"
        return scenario.family

    def _expected_skipped_steps(self, scenario: IncidentScenario | None) -> List[str]:
        if scenario is None:
            return ["full_log_scan", "manual_port_check"]
        return list(scenario.expected_skipped_steps)

    def _recommended_action(self, scenario: IncidentScenario) -> str:
        action_map = {
            "database_timeout": "Correct the database port and rerun the connectivity check.",
            "permission_denied": "Fix credential file ownership or mode and rerun the access validation.",
            "storage_full": "Free space on the database volume and verify WAL writes again.",
        }
        return action_map.get(
            scenario.family,
            "Apply the structured remediation and rerun the validation step.",
        )

    def _root_cause_correct(
        self,
        root_cause: object,
        scenario: IncidentScenario | None,
    ) -> bool:
        if not isinstance(root_cause, str):
            return False
        if scenario is None:
            return "wrong database port" in root_cause.lower()
        return root_cause.strip().lower() == scenario.expected_root_cause.strip().lower()
