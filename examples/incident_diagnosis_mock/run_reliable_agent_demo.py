#!/usr/bin/env python3
"""Run the v1.6 reliable multi-agent incident diagnosis integration demo."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comembus.client import AgentBusClient
from comembus.object_store.lease_manager import ObjectLeaseManager
from comembus.protocol import ObjectRef
from comembus.reliability.dedup import DedupStore
from comembus.server import AgentBusServer
from comembus.state.patch import StatePatch, VersionConflictError
from comembus.state.patch_rebase import PatchRebaser
from comembus.state.sqlite_manager import SQLiteStateManager
from comembus.state.task_state import TaskState

from examples.incident_diagnosis_mock.agents import (
    analyze_config_text,
    analyze_log_blob,
    build_config_state_patch,
    build_initial_task_state,
    build_log_state_patch,
    build_mock_config_text,
    build_mock_log_blob,
    build_review_report_from_state,
)


LOG_TASK_TOPIC = "reliable_incident_log_task"
CONFIG_TASK_TOPIC = "reliable_incident_config_task"
LOG_MESSAGE_ID = "reliable-demo-log-task"
CONFIG_MESSAGE_ID = "reliable-demo-config-task"


def run_reliable_agent_demo(
    visibility_timeout: float = 0.05,
    log_size_bytes: int = 8 * 1024 * 1024,
) -> Dict[str, Any]:
    """Exercise reliable delivery, object GC, rebase, and durable recovery."""

    if visibility_timeout <= 0:
        raise ValueError("visibility_timeout must be positive")

    tempdir = tempfile.TemporaryDirectory(prefix="comembus-reliable-demo-")
    socket_path = os.path.join(tempdir.name, "agent_bus.sock")
    state_path = os.path.join(tempdir.name, "task_state.sqlite")
    dedup_store = DedupStore()
    server = AgentBusServer(
        socket_path,
        visibility_timeout=visibility_timeout,
        dedup_store=dedup_store,
    )
    lease_time = [time.time()]
    lease_manager: Optional[ObjectLeaseManager] = None
    state_manager: Optional[SQLiteStateManager] = None
    clients: List[AgentBusClient] = []
    result: Optional[Dict[str, Any]] = None

    try:
        server.start()
        coordinator = _client(socket_path, "coordinator", clients)
        crashed_log_agent = _client(socket_path, "log-agent-crashed", clients)
        retry_log_agent = _client(socket_path, "log-agent-retry", clients)
        config_agent = _client(socket_path, "config-agent", clients)

        log_blob = build_mock_log_blob(log_size_bytes)
        config_text = build_mock_config_text()
        log_ref = coordinator.object_store.put_bytes(log_blob)
        lease_manager = ObjectLeaseManager(
            object_store=coordinator.object_store,
            default_lease_seconds=0.25,
            clock=lambda: lease_time[0],
        )
        lease_manager.register_object(
            log_ref,
            owner_agent="coordinator",
            consumer_agents=["log-agent-crashed", "log-agent-retry"],
        )

        base_state = build_initial_task_state(
            task_id="reliable-incident-001",
            goal="Diagnose checkout failures with reliable delivery",
            log_ref_dict=log_ref.to_dict(),
        )
        state_manager = SQLiteStateManager(state_path)
        state_manager.create_state(base_state)

        task_payload = {
            "task_state": base_state.to_dict(),
            "object_ref": log_ref.to_dict(),
        }
        coordinator.publish(
            LOG_TASK_TOPIC,
            task_payload,
            message_id=LOG_MESSAGE_ID,
        )
        coordinator.publish(
            CONFIG_TASK_TOPIC,
            {
                "task_state": base_state.to_dict(),
                "config_text": config_text,
            },
            message_id=CONFIG_MESSAGE_ID,
        )

        # The first LogAgent obtains the ObjectRef and then crashes without ACK.
        first_delivery = _wait_for_reliable(
            crashed_log_agent,
            LOG_TASK_TOPIC,
            "log-agent-crashed",
            visibility_timeout,
        )
        first_ref = ObjectRef.from_dict(first_delivery["payload"]["object_ref"])
        lease_manager.acquire(first_ref.object_id, "log-agent-crashed")
        crashed_log_agent.close()

        # ConfigAgent and the eventual LogAgent both build patches from version 1.
        config_delivery = _wait_for_reliable(
            config_agent,
            CONFIG_TASK_TOPIC,
            "config-agent",
            visibility_timeout,
        )
        config_base = TaskState.from_dict(config_delivery["payload"]["task_state"])
        config_patch = _non_conflicting_config_patch(config_base, config_text)
        config_agent.ack(
            config_delivery["message_id"],
            result={"state_patch": config_patch.to_dict()},
        )

        time.sleep(visibility_timeout * 1.5 + 0.01)
        retry_delivery = _wait_for_reliable(
            retry_log_agent,
            LOG_TASK_TOPIC,
            "log-agent-retry",
            visibility_timeout,
        )
        retry_ref = ObjectRef.from_dict(retry_delivery["payload"]["object_ref"])
        retry_base = TaskState.from_dict(retry_delivery["payload"]["task_state"])
        lease_manager.acquire(retry_ref.object_id, "log-agent-retry")
        business_execution_count = 0
        if not dedup_store.is_processed(retry_delivery["message_id"]):
            log_data = retry_log_agent.object_store.get_bytes(retry_ref)
            log_patch = _non_conflicting_log_patch(retry_base, log_data)
            business_execution_count += 1
        else:  # pragma: no cover - this would indicate a broken demo setup
            raise RuntimeError("redelivered log task was processed before business execution")
        lease_manager.release(retry_ref.object_id, "log-agent-retry")
        retry_log_agent.ack(
            retry_delivery["message_id"],
            result={"state_patch": log_patch.to_dict()},
        )

        duplicate_publish = coordinator.publish(
            LOG_TASK_TOPIC,
            task_payload,
            message_id=LOG_MESSAGE_ID,
        )
        duplicate_suppressed = (
            bool(duplicate_publish["duplicate_suppressed"])
            and business_execution_count == 1
            and dedup_store.is_processed(LOG_MESSAGE_ID)
        )

        state_manager.apply_patch(config_patch)
        try:
            state_manager.apply_patch(log_patch)
        except VersionConflictError:
            latest_state = state_manager.snapshot(base_state.task_id)
            rebased_patch = PatchRebaser().rebase(
                log_patch,
                base_state,
                latest_state,
            )
            state_manager.apply_patch(rebased_patch)
            patch_rebased = True
        else:  # pragma: no cover - stale version must be rejected
            patch_rebased = False

        state_manager.close()
        state_manager = None
        restarted_manager = SQLiteStateManager(state_path)
        state_manager = restarted_manager
        recovered = restarted_manager.recover(base_state.task_id)
        state_recovered = (
            recovered.version == 3
            and recovered.facts.get("config_issue") == "database pool too small"
            and recovered.facts.get("log_signal") == "connection pool exhausted"
        )
        report = build_review_report_from_state(recovered)
        root_cause_correct = (
            "database connection pool saturation"
            in str(report["root_cause"]).lower()
        )

        lease_time[0] += 1.0
        reclaimed_ids = lease_manager.collect_expired()
        object_reclaimed = (
            retry_ref.object_id in reclaimed_ids
            and lease_manager.get_stats()["reclaimed_object_count"] == 1
        )
        delivery_stats = server.delivery_manager.get_stats()
        message_requeued = (
            int(retry_delivery["delivery_attempt"]) == 2
            and delivery_stats["message_requeued_count"] >= 1
        )

        result = {
            "message_requeued": message_requeued,
            "duplicate_suppressed": duplicate_suppressed,
            "state_recovered": state_recovered,
            "patch_rebased": patch_rebased,
            "object_reclaimed": object_reclaimed,
            "root_cause_correct": root_cause_correct,
            "delivery_attempts": int(retry_delivery["delivery_attempt"]),
            "business_execution_count": business_execution_count,
            "state_version": recovered.version,
            "root_cause": report["root_cause"],
        }
        if not all(
            bool(result[field])
            for field in (
                "message_requeued",
                "duplicate_suppressed",
                "state_recovered",
                "patch_rebased",
                "object_reclaimed",
                "root_cause_correct",
            )
        ):
            raise RuntimeError(f"reliable demo acceptance failed: {result}")
    finally:
        if state_manager is not None:
            state_manager.close()
        if lease_manager is not None:
            lease_manager.close(force_cleanup=True)
        for client in clients:
            client.close()
        server.stop()
        tempdir.cleanup()

    if result is None:  # pragma: no cover - defensive, exceptions propagate above
        raise RuntimeError("reliable demo produced no result")
    result["shm_residue_count"] = _shm_residue_count()
    if result["shm_residue_count"] != 0:
        raise RuntimeError(
            f"shared-memory residue detected: {result['shm_residue_count']}"
        )
    return result


def _client(
    socket_path: str,
    agent_id: str,
    clients: List[AgentBusClient],
) -> AgentBusClient:
    client = AgentBusClient(socket_path)
    client.register(agent_id)
    clients.append(client)
    return client


def _wait_for_reliable(
    client: AgentBusClient,
    topic: str,
    consumer_agent: str,
    visibility_timeout: float,
    timeout_seconds: float = 3.0,
) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        envelope = client.poll_reliable(
            topic,
            consumer_agent=consumer_agent,
            visibility_timeout=visibility_timeout,
        )
        if envelope is not None:
            return envelope
        time.sleep(0.005)
    raise TimeoutError(f"timed out waiting for reliable topic: {topic}")


def _non_conflicting_log_patch(state: TaskState, log_data: bytes) -> StatePatch:
    original = build_log_state_patch(
        state,
        analyze_log_blob(log_data, state.task_id),
    )
    return StatePatch(
        task_id=original.task_id,
        expected_version=original.expected_version,
        append_fields=original.append_fields,
        merge_dict_fields=original.merge_dict_fields,
    )


def _non_conflicting_config_patch(
    state: TaskState,
    config_text: str,
) -> StatePatch:
    original = build_config_state_patch(
        state,
        analyze_config_text(config_text, state.task_id),
    )
    return StatePatch(
        task_id=original.task_id,
        expected_version=original.expected_version,
        append_fields=original.append_fields,
        merge_dict_fields=original.merge_dict_fields,
    )


def _shm_residue_count() -> int:
    if not os.path.isdir("/dev/shm"):
        return 0
    return sum(
        1 for name in os.listdir("/dev/shm") if name.startswith("comembus_")
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visibility-timeout", type=float, default=0.05)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_reliable_agent_demo(visibility_timeout=args.visibility_timeout)
    for field in (
        "message_requeued",
        "duplicate_suppressed",
        "state_recovered",
        "patch_rebased",
        "object_reclaimed",
        "root_cause_correct",
    ):
        print(f"{field}={str(bool(result[field])).lower()}")
    print("OK: reliable multi-agent demo completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
