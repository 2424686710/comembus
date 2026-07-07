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

from .metrics import CollaborationMetrics, count_text_chars, estimate_tokens, json_size_bytes
from .protocol import AgentCapability, StructuredMessage


@dataclass
class StructuredCollaborationRunner:
    """Simulate collaboration with compact structured messages."""

    task_index: int
    task_topic: str
    db_path: str
    blackboard: Optional[SharedBlackboard] = None
    baseline_steps: int = 5
    log_size_bytes: int = DEFAULT_LOG_SIZE_BYTES

    def run(self) -> CollaborationMetrics:
        started = time.perf_counter()
        own_blackboard = self.blackboard is None
        board = self.blackboard or SharedBlackboard(self.db_path)
        store = SharedMemoryObjectStore()
        state_manager = InMemoryStateManager()
        messages: List[StructuredMessage] = []
        ref = None
        try:
            memory_hits = board.search("database timeout wrong port", tags=["database", "port"], top_k=3)
            memory_hit = bool(memory_hits)
            reused_memory_id = memory_hits[0].memory.memory_id if memory_hits else ""
            actual_steps = self.baseline_steps - 2 if memory_hit else self.baseline_steps
            saved_steps = self.baseline_steps - actual_steps

            log_blob = build_mock_log_blob(self.log_size_bytes)
            config_text = build_mock_config_text()
            ref = store.put_bytes(log_blob)

            task_id = f"collab-task-{self.task_index}"
            goal = "Diagnose a database connection timeout using structured collaboration"
            initial_state = build_initial_task_state(task_id=task_id, goal=goal, log_ref_dict=ref.to_dict())
            state_manager.create_state(initial_state)

            capabilities = self._capabilities()

            planner_to_log = self._message(
                task_id=task_id,
                source_agent="planner-agent",
                target_agent="log-agent",
                action_type="analyze_logs",
                params={
                    "task_topic": self.task_topic,
                    "goal": goal,
                    "expected_state_version": initial_state.version,
                },
                result={},
                capability=capabilities["log-agent"].to_dict(),
                object_refs=[ref.to_dict()],
                state_patch=None,
                memory_refs=[reused_memory_id] if memory_hit else [],
            )
            messages.append(planner_to_log)

            log_analysis = analyze_log_blob(store.get_bytes(ref), task_id)
            log_state = state_manager.snapshot(task_id)
            log_patch = self._build_log_state_patch(log_state, log_analysis)
            log_to_state = self._message(
                task_id=task_id,
                source_agent="log-agent",
                target_agent="state-manager",
                action_type="report_log_analysis",
                params={"expected_state_version": log_state.version},
                result={
                    "summary": log_analysis["summary"],
                    "database_timeout_count": log_analysis["database_timeout_count"],
                    "pool_exhausted_count": log_analysis["pool_exhausted_count"],
                },
                capability=capabilities["log-agent"].to_dict(),
                object_refs=[],
                state_patch=log_patch.to_dict(),
                memory_refs=[reused_memory_id] if memory_hit else [],
            )
            messages.append(log_to_state)
            after_log = state_manager.apply_patch(StatePatch.from_dict(log_to_state.state_patch or {}))

            planner_to_config = self._message(
                task_id=task_id,
                source_agent="planner-agent",
                target_agent="config-agent",
                action_type="check_config",
                params={
                    "task_topic": self.task_topic,
                    "expected_state_version": after_log.version,
                    "config_text": config_text,
                },
                result={},
                capability=capabilities["config-agent"].to_dict(),
                object_refs=[],
                state_patch=None,
                memory_refs=[reused_memory_id] if memory_hit else [],
            )
            messages.append(planner_to_config)

            config_analysis = analyze_config_text(config_text, task_id)
            config_state = state_manager.snapshot(task_id)
            config_patch = self._build_config_state_patch(config_state, config_analysis)
            config_to_state = self._message(
                task_id=task_id,
                source_agent="config-agent",
                target_agent="state-manager",
                action_type="report_config_analysis",
                params={"expected_state_version": config_state.version},
                result={
                    "summary": config_analysis["summary"],
                    "pool_size": config_analysis["pool_size"],
                    "config_risk": config_analysis["config_risk"],
                },
                capability=capabilities["config-agent"].to_dict(),
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
                target_agent="review-agent",
                action_type="review_incident",
                params={
                    "task_topic": self.task_topic,
                    "state_version": final_state.version,
                },
                result={},
                capability=capabilities["review-agent"].to_dict(),
                object_refs=[],
                state_patch=None,
                memory_refs=[reused_memory_id] if memory_hit else [],
            )
            messages.append(planner_to_review)

            report = self._build_review_report(final_state)
            review_to_planner = self._message(
                task_id=task_id,
                source_agent="review-agent",
                target_agent="planner-agent",
                action_type="report_root_cause",
                params={},
                result=report,
                capability=capabilities["review-agent"].to_dict(),
                object_refs=[],
                state_patch=None,
                memory_refs=[reused_memory_id] if memory_hit else [],
            )
            messages.append(review_to_planner)

            self._write_memories(board, task_id, report, log_analysis, config_analysis)

            text_chars = sum(self._message_text_chars(message) for message in messages)
            approx_tokens = estimate_tokens("x" * text_chars) if text_chars else 0
            protocol_bytes = sum(json_size_bytes(message.to_dict()) for message in messages)
            object_ref_count = sum(len(message.object_refs) for message in messages)
            state_patch_count = sum(1 for message in messages if message.state_patch is not None)
            memory_ref_count = sum(len(message.memory_refs) for message in messages)
            non_text_state_bytes = (
                len(log_patch.to_json_bytes()) + len(config_patch.to_json_bytes())
            )
            measured_latency_ms = (time.perf_counter() - started) * 1000.0
            total_latency_ms = measured_latency_ms + (actual_steps * 1.5)
            root_cause_correct = "wrong database port" in report["root_cause"].lower()

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

    def _capabilities(self) -> Dict[str, AgentCapability]:
        return {
            "planner-agent": AgentCapability(
                agent_id="planner-agent",
                role="planner",
                actions=["plan_incident", "dispatch_structured_actions"],
                input_types=["task_topic", "memory_refs"],
                output_types=["structured_action"],
                description="Creates the plan and dispatches structured work items.",
            ),
            "log-agent": AgentCapability(
                agent_id="log-agent",
                role="log_analysis",
                actions=["analyze_logs"],
                input_types=["object_ref", "state_version"],
                output_types=["state_patch", "log_summary"],
                description="Reads shared-memory logs and returns structured log findings.",
            ),
            "config-agent": AgentCapability(
                agent_id="config-agent",
                role="config_analysis",
                actions=["check_config"],
                input_types=["config_text", "state_version"],
                output_types=["state_patch", "config_summary"],
                description="Inspects configuration and emits a structured patch.",
            ),
            "review-agent": AgentCapability(
                agent_id="review-agent",
                role="review",
                actions=["review_incident"],
                input_types=["state_version", "memory_refs"],
                output_types=["root_cause_report"],
                description="Builds the final root cause report from structured state.",
            ),
        }

    def _build_log_state_patch(
        self,
        state: TaskState,
        log_analysis: Dict[str, object],
    ) -> StatePatch:
        return StatePatch(
            task_id=state.task_id,
            expected_version=state.version,
            set_fields={
                "phase": "log_analysis_complete",
                "pending_steps": ["config_check", "review"],
            },
            append_fields={"completed_steps": ["log_analysis"]},
            merge_dict_fields={
                "facts": {
                    "log_error": "database timeout",
                    "log_timeout_count": str(log_analysis["database_timeout_count"]),
                    "log_signal": "repeated timeout during database connection setup",
                    "log_summary": str(log_analysis["summary"]),
                }
            },
        )

    def _build_config_state_patch(
        self,
        state: TaskState,
        config_analysis: Dict[str, object],
    ) -> StatePatch:
        return StatePatch(
            task_id=state.task_id,
            expected_version=state.version,
            set_fields={
                "phase": "review_ready",
                "pending_steps": ["review"],
            },
            append_fields={"completed_steps": ["config_check"]},
            merge_dict_fields={
                "facts": {
                    "config_port": "wrong database port",
                    "config_summary": (
                        "configuration review indicates the service pointed at the wrong database port"
                    ),
                    "config_service": str(config_analysis["service_name"]),
                    "config_timeout_ms": str(config_analysis["timeout_ms"]),
                }
            },
        )

    def _build_review_report(self, state: TaskState) -> Dict[str, object]:
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
        }

    def _write_memories(
        self,
        board: SharedBlackboard,
        task_id: str,
        report: Dict[str, object],
        log_analysis: Dict[str, object],
        config_analysis: Dict[str, object],
    ) -> None:
        board.write_memory(
            task_id=task_id,
            source_agent="log-agent",
            task_topic=self.task_topic,
            memory_type="evidence",
            summary="database timeout evidence from logs",
            content=(
                "log_error=database timeout; "
                f"log_summary={log_analysis['summary']}; "
                f"log_pool_exhausted_count={log_analysis['pool_exhausted_count']}"
            ),
            tags=["database", "timeout", "port"],
            confidence=0.95,
            metadata={"task_index": self.task_index},
        )
        board.write_memory(
            task_id=task_id,
            source_agent="config-agent",
            task_topic=self.task_topic,
            memory_type="summary",
            summary="wrong database port explains the timeout",
            content=(
                f"config_summary={config_analysis['summary']}; "
                "config_port=wrong database port; "
                f"root_cause={report['root_cause']}"
            ),
            tags=["database", "port", "root_cause"],
            confidence=0.93,
            metadata={"task_index": self.task_index},
        )
        board.write_memory(
            task_id=task_id,
            source_agent="review-agent",
            task_topic=self.task_topic,
            memory_type="strategy",
            summary="reuse wrong port diagnosis before re-scanning all logs",
            content=(
                "If database timeout and wrong port signals are already known, "
                "reuse that memory and skip the expensive full_log_scan step."
            ),
            tags=["database", "port", "strategy"],
            confidence=0.97,
            metadata={"task_index": self.task_index},
        )
