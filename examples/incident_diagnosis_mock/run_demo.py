#!/usr/bin/env python3
"""Run the mock multi-agent incident diagnosis demo."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
from typing import Iterable, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comembus.client import AgentBusClient
from comembus.object_store.shm_store import ObjectStoreError
from comembus.server import AgentBusServer
from examples.incident_diagnosis_mock.agents import (
    DEFAULT_LOG_SIZE_BYTES,
    PlannerAgent,
    LogAgent,
    ConfigAgent,
    ReviewAgent,
    REVIEW_REPORTS_TOPIC,
    build_mock_config_text,
    build_mock_log_blob,
    start_agent_process,
    wait_for_topic_message,
)


def terminate_processes(processes: Iterable) -> None:
    for process in processes:
        if process.is_alive():
            process.terminate()
    for process in processes:
        process.join(timeout=2.0)


def join_processes(processes: Iterable, timeout_seconds: float = 10.0) -> None:
    for process in processes:
        process.join(timeout=timeout_seconds)
        if process.is_alive():
            raise RuntimeError(f"agent process did not exit in time: {process.name}")
        if process.exitcode != 0:
            raise RuntimeError(
                f"agent process failed: {process.name} exitcode={process.exitcode}"
            )


def main() -> int:
    tempdir = tempfile.TemporaryDirectory(prefix="comembus-agent-demo-")
    socket_path = os.path.join(tempdir.name, "comembus.sock")
    incident_id = "INC-2026-0707-db-pool"
    server = AgentBusServer(socket_path)
    coordinator = None
    processes: List = []
    log_ref = None

    try:
        server.start()
        coordinator = AgentBusClient(socket_path)
        coordinator.register("demo-coordinator")

        log_blob = build_mock_log_blob(DEFAULT_LOG_SIZE_BYTES)
        config_text = build_mock_config_text()
        log_ref = coordinator.object_store.put_bytes(log_blob)

        processes = [
            start_agent_process(
                LogAgent(socket_path=socket_path),
                "log-agent-process",
            ),
            start_agent_process(
                ConfigAgent(socket_path=socket_path),
                "config-agent-process",
            ),
            start_agent_process(
                ReviewAgent(socket_path=socket_path),
                "review-agent-process",
            ),
            start_agent_process(
                PlannerAgent(
                    socket_path=socket_path,
                    incident_id=incident_id,
                    log_ref_dict=log_ref.to_dict(),
                    config_text=config_text,
                ),
                "planner-agent-process",
            ),
        ]

        report = wait_for_topic_message(
            coordinator,
            REVIEW_REPORTS_TOPIC,
            timeout_seconds=15.0,
        )
        join_processes(processes, timeout_seconds=15.0)

        root_cause = str(report.get("root_cause", ""))
        if "database connection pool saturation" not in root_cause.lower():
            raise RuntimeError("review report did not contain the expected root cause")

        print(f"[Coordinator] Final root cause: {root_cause}", flush=True)
        print("OK: mock multi-agent incident diagnosis completed")
        return 0
    finally:
        if processes:
            terminate_processes(processes)
        if log_ref is not None and coordinator is not None:
            try:
                coordinator.object_store.unlink(log_ref)
            except ObjectStoreError:
                pass
        if coordinator is not None:
            coordinator.close()
        server.stop()
        tempdir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())

