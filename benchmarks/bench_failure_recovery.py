#!/usr/bin/env python3
"""Systematic failure injection benchmark for CoMemBus v1.4 recovery paths."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import os
from multiprocessing import shared_memory
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
from typing import Callable, Dict, Iterable, List, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comembus.client import AgentBusClient
from comembus.codeact.sandbox import run_code_sandbox
from comembus.llm.adapter import LLMMessage
from comembus.llm.local_http_client import LocalHTTPChatClient
from comembus.object_store.lease_manager import ObjectLeaseManager
from comembus.object_store.lifecycle import ACTIVE
from comembus.object_store.shm_store import SharedMemoryObjectStore
from comembus.reliability.failure_injector import FailureInjector, InjectedFailure
from comembus.server import AgentBusServer
from comembus.state.patch import StatePatch, VersionConflictError
from comembus.state.patch_rebase import PatchRebaser
from comembus.state.sqlite_manager import SQLiteStateManager
from comembus.state.task_state import TaskState


CSV_FIELDS = [
    "scenario",
    "success",
    "recovery_time_ms",
    "delivery_attempts",
    "duplicate_suppressed",
    "message_requeued",
    "state_recovered",
    "object_reclaimed",
    "shm_residue_count",
    "error",
]


@dataclass
class FailureResult:
    scenario: str
    success: bool
    recovery_time_ms: float
    delivery_attempts: int = 0
    duplicate_suppressed: bool = False
    message_requeued: bool = False
    state_recovered: bool = False
    object_reclaimed: bool = False
    shm_residue_count: int = 0
    error: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "scenario": self.scenario,
            "success": self.success,
            "recovery_time_ms": self.recovery_time_ms,
            "delivery_attempts": self.delivery_attempts,
            "duplicate_suppressed": self.duplicate_suppressed,
            "message_requeued": self.message_requeued,
            "state_recovered": self.state_recovered,
            "object_reclaimed": self.object_reclaimed,
            "shm_residue_count": self.shm_residue_count,
            "error": self.error,
        }


def run_benchmark() -> List[FailureResult]:
    scenarios: List[tuple[str, Callable[[], Mapping[str, object]]]] = [
        ("consumer_crash_redelivery", _consumer_crash_redelivery),
        ("duplicate_message_suppression", _duplicate_message_suppression),
        ("object_lease_crash_reclaim", _object_lease_crash_reclaim),
        ("concurrent_patch_rebase", _concurrent_patch_rebase),
        ("coordinator_crash_state_recovery", _coordinator_crash_recovery),
        ("sqlite_locked_retry", _sqlite_locked_retry),
        ("llm_endpoint_fallback", _llm_endpoint_fallback),
        ("codeact_timeout_continue", _codeact_timeout_continue),
    ]
    return [_run_one(name, scenario) for name, scenario in scenarios]


def _run_one(
    name: str, scenario: Callable[[], Mapping[str, object]]
) -> FailureResult:
    started = time.perf_counter()
    try:
        values = dict(scenario())
        success = bool(values.pop("success", True))
        error = str(values.pop("error", ""))
    except Exception as exc:
        values = {}
        success = False
        error = f"{type(exc).__name__}: {exc}"
    residue_count = _shm_residue_count()
    if residue_count:
        success = False
        error = error or f"shared-memory residue detected: {residue_count}"
    return FailureResult(
        scenario=name,
        success=success,
        recovery_time_ms=(time.perf_counter() - started) * 1000.0,
        delivery_attempts=int(values.get("delivery_attempts", 0)),
        duplicate_suppressed=bool(values.get("duplicate_suppressed", False)),
        message_requeued=bool(values.get("message_requeued", False)),
        state_recovered=bool(values.get("state_recovered", False)),
        object_reclaimed=bool(values.get("object_reclaimed", False)),
        shm_residue_count=residue_count,
        error=error,
    )


def _consumer_crash_redelivery() -> Mapping[str, object]:
    with tempfile.TemporaryDirectory(prefix="comembus-failure-delivery-") as directory:
        socket_path = os.path.join(directory, "bus.sock")
        server = AgentBusServer(socket_path, visibility_timeout=0.03)
        server.start()
        producer = None
        crashed = None
        replacement = None
        try:
            producer = AgentBusClient(socket_path)
            crashed = AgentBusClient(socket_path)
            replacement = AgentBusClient(socket_path)
            producer.publish("jobs", {"work": "recover"}, message_id="failure-msg-1")
            first = crashed.poll_reliable("jobs", consumer_agent="consumer-a")
            if first is None or first["delivery_attempt"] != 1:
                raise RuntimeError("first reliable delivery was not observed")
            # Crash: close without ACK. The server must expose it again after timeout.
            crashed.close()
            time.sleep(0.04)
            second = replacement.poll_reliable("jobs", consumer_agent="consumer-b")
            if second is None or second["delivery_attempt"] != 2:
                raise RuntimeError("message was not redelivered after visibility timeout")
            replacement.ack(str(second["message_id"]), {"status": "completed"})
            stats = server.delivery_manager.get_stats()
            return {
                "success": stats["message_requeued_count"] == 1,
                "delivery_attempts": int(second["delivery_attempt"]),
                "message_requeued": stats["message_requeued_count"] == 1,
            }
        finally:
            for client in (producer, crashed, replacement):
                if client is not None:
                    client.close()
            server.stop()


def _duplicate_message_suppression() -> Mapping[str, object]:
    with tempfile.TemporaryDirectory(prefix="comembus-failure-dedup-") as directory:
        socket_path = os.path.join(directory, "bus.sock")
        server = AgentBusServer(socket_path)
        server.start()
        producer = None
        consumer = None
        executions = 0
        try:
            producer = AgentBusClient(socket_path)
            consumer = AgentBusClient(socket_path)
            producer.publish("jobs", {"work": "once"}, message_id="dedup-msg-1")
            envelope = consumer.poll_reliable("jobs", consumer_agent="worker")
            if envelope is None:
                raise RuntimeError("dedup scenario did not receive the original message")
            executions += 1
            expected_result = {"executions": executions}
            consumer.ack(str(envelope["message_id"]), expected_result)
            duplicate = producer.publish(
                "jobs", {"work": "once"}, message_id="dedup-msg-1"
            )
            if consumer.poll_reliable("jobs", consumer_agent="worker") is not None:
                executions += 1
            suppressed = bool(duplicate["duplicate_suppressed"])
            result_reused = duplicate["processed_result"] == expected_result
            return {
                "success": suppressed and result_reused and executions == 1,
                "delivery_attempts": int(envelope["delivery_attempt"]),
                "duplicate_suppressed": suppressed,
            }
        finally:
            for client in (producer, consumer):
                if client is not None:
                    client.close()
            server.stop()


def _object_lease_crash_reclaim() -> Mapping[str, object]:
    clock = _FakeClock(1000.0)
    store = SharedMemoryObjectStore()
    manager = ObjectLeaseManager(store, default_lease_seconds=5.0, clock=clock)
    ref = store.put_bytes(b"lease-failure-payload" * 128)
    registered = False
    try:
        manager.register_object(
            ref,
            owner_agent="producer",
            consumer_agents=["consumer-a"],
        )
        registered = True
        manager.acquire(ref.object_id, "consumer-a")
        # Crash: no release. Advancing the deterministic clock expires the lease.
        clock.advance(6.0)
        reclaimed = manager.collect_expired()
        try:
            probe = shared_memory.SharedMemory(name=ref.shm_name, create=False)
        except FileNotFoundError:
            object_absent = True
        else:
            probe.close()
            object_absent = False
        stats = manager.get_stats()
        return {
            "success": (
                ref.object_id in reclaimed
                and object_absent
                and stats["leaked_object_count"] == 1
                and stats["reclaimed_object_count"] == 1
            ),
            "object_reclaimed": ref.object_id in reclaimed and object_absent,
        }
    finally:
        if registered and manager.get_record(ref.object_id).state == ACTIVE:
            manager.force_cleanup(ref.object_id)
        elif not registered:
            store.unlink(ref)


def _concurrent_patch_rebase() -> Mapping[str, object]:
    with tempfile.TemporaryDirectory(prefix="comembus-failure-rebase-") as directory:
        manager = SQLiteStateManager(os.path.join(directory, "state.sqlite"))
        try:
            base = manager.create_state(_base_state("rebase-task"))
            patch_a = StatePatch(
                task_id=base.task_id,
                expected_version=base.version,
                set_fields={"phase": "log_complete"},
            )
            patch_b = StatePatch(
                task_id=base.task_id,
                expected_version=base.version,
                append_fields={"completed_steps": ["config_check"]},
                merge_dict_fields={"facts": {"config_issue": "wrong port"}},
            )
            latest = manager.apply_patch(patch_a)
            conflict_seen = False
            try:
                manager.apply_patch(patch_b)
            except VersionConflictError:
                conflict_seen = True
            if not conflict_seen:
                raise RuntimeError("stale concurrent patch was accepted without rebase")
            rebased = PatchRebaser().rebase(patch_b, base, latest)
            final = manager.apply_patch(rebased)
            return {
                "success": (
                    final.version == 3
                    and final.phase == "log_complete"
                    and final.facts["config_issue"] == "wrong port"
                ),
                "state_recovered": True,
            }
        finally:
            manager.close()


def _coordinator_crash_recovery() -> Mapping[str, object]:
    injector = FailureInjector({"after_patch_commit": 1})
    with tempfile.TemporaryDirectory(prefix="comembus-failure-restart-") as directory:
        path = os.path.join(directory, "state.sqlite")
        manager = SQLiteStateManager(path)
        base = manager.create_state(_base_state("restart-task"))
        patch = StatePatch(
            task_id=base.task_id,
            expected_version=base.version,
            set_fields={"phase": "review_ready"},
            merge_dict_fields={"facts": {"durable": "yes"}},
        )
        crash_seen = False
        try:
            manager.apply_patch(patch)
            injector.trigger("after_patch_commit")
        except InjectedFailure:
            crash_seen = True
        finally:
            manager.close()
        if not crash_seen:
            raise RuntimeError("coordinator crash was not injected")
        restarted = SQLiteStateManager(path)
        try:
            recovered = restarted.recover(base.task_id)
            state_recovered = (
                recovered.version == 2
                and recovered.phase == "review_ready"
                and recovered.facts["durable"] == "yes"
            )
            return {"success": state_recovered, "state_recovered": state_recovered}
        finally:
            restarted.close()


def _sqlite_locked_retry() -> Mapping[str, object]:
    with tempfile.TemporaryDirectory(prefix="comembus-failure-locked-") as directory:
        path = os.path.join(directory, "state.sqlite")
        manager = SQLiteStateManager(
            path, max_retries=20, retry_delay_seconds=0.01
        )
        base = manager.create_state(_base_state("locked-task"))
        blocker = sqlite3.connect(
            path, timeout=0.0, isolation_level=None, check_same_thread=False
        )
        blocker.execute("BEGIN IMMEDIATE")

        def release_lock() -> None:
            time.sleep(0.05)
            blocker.execute("ROLLBACK")

        release_thread = threading.Thread(target=release_lock)
        release_thread.start()
        try:
            updated = manager.apply_patch(
                StatePatch(
                    task_id=base.task_id,
                    expected_version=base.version,
                    merge_dict_fields={"facts": {"retry_succeeded": "true"}},
                )
            )
            release_thread.join()
            return {
                "success": (
                    updated.version == 2
                    and manager.last_retry_count > 0
                    and updated.facts["retry_succeeded"] == "true"
                ),
                "state_recovered": True,
            }
        finally:
            release_thread.join()
            if blocker.in_transaction:
                blocker.execute("ROLLBACK")
            blocker.close()
            manager.close()


def _llm_endpoint_fallback() -> Mapping[str, object]:
    with tempfile.TemporaryDirectory(prefix="comembus-failure-llm-") as directory:
        manager = SQLiteStateManager(os.path.join(directory, "state.sqlite"))
        base = manager.create_state(_base_state("llm-task"))
        try:
            client = LocalHTTPChatClient(
                endpoint="http://127.0.0.1:1/v1/chat/completions",
                model="unreachable-model",
                timeout_seconds=0.05,
            )
            response = client.generate(
                [
                    LLMMessage(
                        role="user",
                        content="database timeout and wrong database port",
                    )
                ]
            )
            if not response.used_fallback or response.provider != "mock":
                raise RuntimeError("unreachable LLM did not use explicit fallback")
            updated = manager.apply_patch(
                StatePatch(
                    task_id=base.task_id,
                    expected_version=base.version,
                    merge_dict_fields={
                        "facts": {
                            "review_provider": response.provider,
                            "fallback_used": "true",
                        }
                    },
                )
            )
            recovered = manager.recover(base.task_id)
            ok = updated.version == 2 and recovered.to_dict() == updated.to_dict()
            return {"success": ok, "state_recovered": ok}
        finally:
            manager.close()


def _codeact_timeout_continue() -> Mapping[str, object]:
    with tempfile.TemporaryDirectory(prefix="comembus-failure-codeact-") as directory:
        manager = SQLiteStateManager(os.path.join(directory, "state.sqlite"))
        base = manager.create_state(_base_state("codeact-task"))
        try:
            result = run_code_sandbox(
                "result = 0\nfor i in range(10 ** 9):\n    result = result + i",
                inputs={},
                timeout_sec=0.05,
            )
            if not result["timeout"] or result["ok"]:
                raise RuntimeError("CodeAct timeout was not surfaced")
            continued = manager.apply_patch(
                StatePatch(
                    task_id=base.task_id,
                    expected_version=base.version,
                    set_fields={"phase": "review_ready"},
                    merge_dict_fields={"facts": {"codeact_status": "timed_out"}},
                )
            )
            ok = (
                continued.version == 2
                and continued.phase == "review_ready"
                and continued.facts["codeact_status"] == "timed_out"
            )
            return {"success": ok, "state_recovered": ok}
        finally:
            manager.close()


def _base_state(task_id: str) -> TaskState:
    return TaskState(
        task_id=task_id,
        version=1,
        goal="complete failure recovery workflow",
        phase="started",
        completed_steps=[],
        pending_steps=["recover", "review"],
        facts={"input": "stable"},
        errors=[],
        artifacts={},
    )


class _FakeClock:
    def __init__(self, initial: float) -> None:
        self.value = float(initial)

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += float(seconds)


def _shm_residue_count() -> int:
    shm_dir = Path("/dev/shm")
    if not shm_dir.is_dir():
        return 0
    return sum(1 for path in shm_dir.glob("comembus_*"))


def write_results(path: str | Path, rows: Iterable[FailureResult]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for result in rows:
            row = result.to_dict()
            for field in (
                "success",
                "duplicate_suppressed",
                "message_requeued",
                "state_recovered",
                "object_reclaimed",
            ):
                row[field] = str(bool(row[field])).lower()
            row["recovery_time_ms"] = f"{float(row['recovery_time_ms']):.6f}"
            writer.writerow(row)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="results/failure_injection.csv")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = run_benchmark()
    write_results(args.output, rows)
    for row in rows:
        print(
            f"{row.scenario}: success={str(row.success).lower()} "
            f"recovery_time_ms={row.recovery_time_ms:.3f}"
        )
    if not all(row.success for row in rows):
        for row in rows:
            if not row.success:
                print(f"failure scenario failed: {row.scenario}: {row.error}", file=sys.stderr)
        return 1
    print(f"wrote {len(rows)} failure injection rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
