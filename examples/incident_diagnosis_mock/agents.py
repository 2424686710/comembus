"""Mock agents for the CoMemBus incident diagnosis demo."""

from __future__ import annotations

from dataclasses import dataclass
import multiprocessing
from pathlib import Path
import sys
import time
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comembus.client import AgentBusClient
from comembus.protocol import ObjectRef
from comembus.state.patch import StatePatch, apply_patch
from comembus.state.task_state import TaskState
from comembus.transport.adaptive import AdaptiveTransportPolicy


INITIAL_STATE_TOPIC = "incident_initial_state"
TASKS_LOG_TOPIC = "incident_tasks_log"
TASKS_CONFIG_TOPIC = "incident_tasks_config"
LOG_PATCHES_TOPIC = "incident_log_patches"
CONFIG_PATCHES_TOPIC = "incident_config_patches"
REVIEW_TASKS_TOPIC = "incident_review_tasks"
REVIEW_REPORTS_TOPIC = "incident_review_reports"

DEFAULT_LOG_SIZE_BYTES = 8 * 1024 * 1024


def build_mock_config_text() -> str:
    return "\n".join(
        [
            "service.name=checkout-api",
            "database.host=db-primary.internal",
            "database.pool_size=4",
            "request.timeout_ms=250",
            "retry.enabled=false",
            "feature.flag.safe_mode=false",
        ]
    )


def build_mock_log_blob(size_bytes: int = DEFAULT_LOG_SIZE_BYTES) -> bytes:
    if size_bytes < DEFAULT_LOG_SIZE_BYTES:
        raise ValueError(
            f"log size must be at least {DEFAULT_LOG_SIZE_BYTES} bytes for the demo"
        )

    lines = [
        "INFO checkout-api request_id=warmup-0001 status=200 latency_ms=18",
        "INFO checkout-api request_id=warmup-0002 status=200 latency_ms=23",
        "WARN checkout-api ConnectionPoolExhausted pool_size=4 waiting_requests=27",
        "ERROR checkout-api DatabaseTimeout acquire connection timed out after 250ms",
        "ERROR checkout-api RequestFailed route=/checkout error=DatabaseTimeout",
        "INFO payment-worker sync=heartbeat status=ok",
    ]
    block = ("\n".join(lines) + "\n").encode("utf-8")
    repeat_count = (size_bytes // len(block)) + 1
    payload = block * repeat_count
    return payload[:size_bytes]


def analyze_log_blob(data: bytes, incident_id: str) -> Dict[str, Any]:
    text = data.decode("utf-8", errors="replace")
    timeout_count = text.count("DatabaseTimeout")
    pool_exhausted_count = text.count("ConnectionPoolExhausted")
    request_failed_count = text.count("RequestFailed")

    suspected_component = "database_pool"
    if pool_exhausted_count == 0 and timeout_count == 0:
        suspected_component = "unknown"

    return {
        "incident_id": incident_id,
        "log_size_bytes": len(data),
        "database_timeout_count": timeout_count,
        "pool_exhausted_count": pool_exhausted_count,
        "request_failed_count": request_failed_count,
        "suspected_component": suspected_component,
        "summary": (
            "database connection pool saturation is visible in the checkout logs"
            if suspected_component == "database_pool"
            else "no strong signal found in logs"
        ),
    }


def analyze_config_text(config_text: str, incident_id: str) -> Dict[str, Any]:
    values: Dict[str, str] = {}
    for raw_line in config_text.splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()

    pool_size = int(values.get("database.pool_size", "0"))
    timeout_ms = int(values.get("request.timeout_ms", "0"))
    retry_enabled = values.get("retry.enabled", "false").lower() == "true"
    safe_mode_enabled = values.get("feature.flag.safe_mode", "false").lower() == "true"

    config_risk = "database_pool_too_small" if 0 < pool_size <= 4 else "no_clear_risk"
    return {
        "incident_id": incident_id,
        "service_name": values.get("service.name", "unknown"),
        "database_host": values.get("database.host", "unknown"),
        "pool_size": pool_size,
        "timeout_ms": timeout_ms,
        "retry_enabled": retry_enabled,
        "safe_mode_enabled": safe_mode_enabled,
        "config_risk": config_risk,
        "summary": (
            "database pool size is undersized for checkout traffic"
            if config_risk == "database_pool_too_small"
            else "config does not show an obvious database pool risk"
        ),
    }


def build_initial_task_state(task_id: str, goal: str, log_ref_dict: Dict[str, Any]) -> TaskState:
    return TaskState(
        task_id=task_id,
        version=1,
        goal=goal,
        phase="planning",
        completed_steps=[],
        pending_steps=["log_analysis", "config_check", "review"],
        facts={},
        errors=[],
        artifacts={
            "log_bundle": {
                "kind": "object_ref",
                "object_ref": dict(log_ref_dict),
            }
        },
    )


def build_log_state_patch(state: TaskState, log_analysis: Dict[str, Any]) -> StatePatch:
    incident_id = str(log_analysis["incident_id"])
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
                "incident_id": incident_id,
                "log_error": "database timeout",
                "log_signal": "connection pool exhausted",
                "log_component": str(log_analysis["suspected_component"]),
                "log_timeout_count": str(log_analysis["database_timeout_count"]),
                "log_pool_exhausted_count": str(log_analysis["pool_exhausted_count"]),
                "log_summary": str(log_analysis["summary"]),
            }
        },
    )


def build_config_state_patch(
    state: TaskState,
    config_analysis: Dict[str, Any],
) -> StatePatch:
    issue = "database pool too small"
    if str(config_analysis["config_risk"]) != "database_pool_too_small":
        issue = "no clear config issue"
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
                "config_issue": issue,
                "config_service": str(config_analysis["service_name"]),
                "config_pool_size": str(config_analysis["pool_size"]),
                "config_timeout_ms": str(config_analysis["timeout_ms"]),
                "config_summary": str(config_analysis["summary"]),
            }
        },
    )


def build_review_report_from_state(state: TaskState) -> Dict[str, Any]:
    log_error = state.facts.get("log_error", "").lower()
    config_issue = state.facts.get("config_issue", "").lower()
    config_pool_size = state.facts.get("config_pool_size", "unknown")

    if "database timeout" in log_error and "pool too small" in config_issue:
        root_cause = (
            "Checkout failures were caused by database connection pool saturation "
            f"with pool_size={config_pool_size}."
        )
        confidence = "high"
        remediation = (
            "Increase database.pool_size, revisit request timeout, and enable a safer retry path."
        )
    else:
        root_cause = "The available mock signals are insufficient to isolate a single root cause."
        confidence = "medium"
        remediation = "Collect more logs and configuration state before taking action."

    return {
        "incident_id": state.task_id,
        "service_name": state.facts.get("config_service", "unknown"),
        "root_cause": root_cause,
        "confidence": confidence,
        "evidence": [
            state.facts.get("log_summary", ""),
            state.facts.get("config_summary", ""),
        ],
        "recommended_action": remediation,
        "state_version": state.version,
    }


def summarize_incident(incident_id: str, log_blob: bytes, config_text: str) -> Dict[str, Any]:
    initial_state = build_initial_task_state(
        task_id=incident_id,
        goal="Diagnose checkout failures from logs and config",
        log_ref_dict={"object_id": "mock", "shm_name": "mock", "size": len(log_blob)},
    )
    log_patch = build_log_state_patch(initial_state, analyze_log_blob(log_blob, incident_id))
    after_log = apply_patch(initial_state, log_patch)
    config_patch = build_config_state_patch(
        after_log, analyze_config_text(config_text, incident_id)
    )
    final_state = apply_patch(after_log, config_patch)
    return build_review_report_from_state(final_state)


def wait_for_topic_message(
    client: AgentBusClient,
    topic: str,
    timeout_seconds: float = 10.0,
    poll_interval_seconds: float = 0.05,
) -> Dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        message = client.poll(topic)
        if message is not None:
            return message
        time.sleep(poll_interval_seconds)
    raise TimeoutError(f"timed out waiting for topic: {topic}")


def log_agent_message(agent_name: str, message: str) -> None:
    print(f"[{agent_name}] {message}", flush=True)


@dataclass(frozen=True)
class PlannerAgent:
    socket_path: str
    incident_id: str
    log_ref_dict: Dict[str, Any]
    config_text: str
    timeout: float = 10.0

    def run(self) -> None:
        client = AgentBusClient(self.socket_path, timeout=self.timeout)
        try:
            client.register("planner-agent")
            policy = AdaptiveTransportPolicy()
            log_mode = policy.choose_mode(
                size_bytes=int(self.log_ref_dict["size"]),
                receivers=1,
            )
            config_mode = policy.choose_mode(
                size_bytes=len(self.config_text.encode("utf-8")),
                receivers=1,
            )
            state = build_initial_task_state(
                task_id=self.incident_id,
                goal="Diagnose checkout failures from logs and config",
                log_ref_dict=self.log_ref_dict,
            )
            log_agent_message(
                "PlannerAgent",
                f"created initial state version={state.version} "
                f"with log transport={log_mode} and config transport={config_mode}",
            )
            client.publish(
                INITIAL_STATE_TOPIC,
                {
                    "task_state": state.to_dict(),
                    "object_ref": dict(self.log_ref_dict),
                    "config_text": self.config_text,
                    "selected_mode_log": log_mode,
                    "selected_mode_config": config_mode,
                },
            )
            log_agent_message("PlannerAgent", "initial TaskState published")
        finally:
            client.close()


@dataclass(frozen=True)
class LogAgent:
    socket_path: str
    timeout: float = 10.0

    def run(self) -> None:
        client = AgentBusClient(self.socket_path, timeout=self.timeout)
        try:
            client.register("log-agent")
            log_agent_message("LogAgent", "waiting for log analysis task")
            task = wait_for_topic_message(client, TASKS_LOG_TOPIC, timeout_seconds=self.timeout)
            state = TaskState.from_dict(task["task_state"])
            ref = ObjectRef.from_dict(task["object_ref"])
            log_agent_message(
                "LogAgent",
                f"received task_state version={state.version} and ObjectRef size={ref.size}",
            )
            data = client.object_store.get_bytes(ref)
            analysis = analyze_log_blob(data, state.task_id)
            patch = build_log_state_patch(state, analysis)
            log_agent_message(
                "LogAgent",
                f"generated patch expected_version={patch.expected_version}",
            )
            client.publish(LOG_PATCHES_TOPIC, {"state_patch": patch.to_dict()})
            log_agent_message("LogAgent", "published state patch for log analysis")
        finally:
            client.close()


@dataclass(frozen=True)
class ConfigAgent:
    socket_path: str
    timeout: float = 10.0

    def run(self) -> None:
        client = AgentBusClient(self.socket_path, timeout=self.timeout)
        try:
            client.register("config-agent")
            log_agent_message("ConfigAgent", "waiting for config analysis task")
            task = wait_for_topic_message(
                client, TASKS_CONFIG_TOPIC, timeout_seconds=self.timeout
            )
            state = TaskState.from_dict(task["task_state"])
            config_text = str(task["config_text"])
            analysis = analyze_config_text(config_text, state.task_id)
            patch = build_config_state_patch(state, analysis)
            log_agent_message(
                "ConfigAgent",
                f"generated patch expected_version={patch.expected_version}",
            )
            client.publish(CONFIG_PATCHES_TOPIC, {"state_patch": patch.to_dict()})
            log_agent_message("ConfigAgent", "published state patch for config analysis")
        finally:
            client.close()


@dataclass(frozen=True)
class ReviewAgent:
    socket_path: str
    timeout: float = 10.0

    def run(self) -> None:
        client = AgentBusClient(self.socket_path, timeout=self.timeout)
        try:
            client.register("review-agent")
            log_agent_message("ReviewAgent", "waiting for final TaskState")
            task = wait_for_topic_message(client, REVIEW_TASKS_TOPIC, timeout_seconds=self.timeout)
            state = TaskState.from_dict(task["task_state"])
            log_agent_message(
                "ReviewAgent",
                f"received final TaskState version={state.version} facts={state.facts}",
            )
            report = build_review_report_from_state(state)
            client.publish(REVIEW_REPORTS_TOPIC, report)
            log_agent_message(
                "ReviewAgent",
                f"published final review report for task {state.task_id}",
            )
        finally:
            client.close()


def run_agent_process(agent: Any) -> None:
    agent.run()


def start_agent_process(agent: Any, process_name: str) -> multiprocessing.Process:
    process = multiprocessing.Process(
        target=run_agent_process,
        args=(agent,),
        name=process_name,
    )
    process.start()
    return process
