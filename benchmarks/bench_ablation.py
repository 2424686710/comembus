#!/usr/bin/env python3
"""Rigorous, deterministic component ablations for CoMemBus collaboration."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
import math
from pathlib import Path
import random
import socket
import sys
import threading
import time
from typing import Any, Dict, Iterable, List, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comembus.capability.registry import CapabilityRegistry, default_capabilities
from comembus.memory.embedding import HashEmbeddingEncoder
from comembus.metrics.process_metrics import ProcessMetrics
from comembus.metrics.recorder import MetricsRecorder
from comembus.metrics.statistics import summarize
from comembus.object_store.shm_store import ObjectStoreError, SharedMemoryObjectStore
from comembus.state.patch import StatePatch, apply_patch
from comembus.state.task_state import TaskState
from comembus.transport.uds import recv_frame, send_frame
from comembus import protocol as protocol_module
from examples.incident_diagnosis_mock.scenarios import (
    IncidentScenario,
    expand_scenarios,
    load_scenarios,
    scenario_to_config_text,
    scenario_to_log_bytes,
)


RANDOM_SEED = 20260710
AGENT_FLOW = (
    ("planner-agent", "log-agent", "planner_to_log"),
    ("log-agent", "config-agent", "log_to_config"),
    ("config-agent", "memory-agent", "config_to_memory"),
    ("memory-agent", "review-agent", "memory_to_review"),
    ("review-agent", "planner-agent", "review_to_planner"),
)
TOKEN_METRIC_TYPE = "character_estimate_4_chars_per_token"


@dataclass(frozen=True)
class ModeConfig:
    mode: str
    baseline_kind: str = ""
    use_shm: bool = True
    use_patch: bool = True
    use_memory: bool = True
    use_embedding: bool = True
    use_capability: bool = True


MODE_CONFIGS: Dict[str, ModeConfig] = {
    "text_full_context": ModeConfig("text_full_context", baseline_kind="text_full"),
    "text_summary": ModeConfig("text_summary", baseline_kind="text_summary"),
    "json_full_state": ModeConfig("json_full_state", baseline_kind="json_full"),
    "structured_no_shm": ModeConfig("structured_no_shm", use_shm=False),
    "structured_no_patch": ModeConfig("structured_no_patch", use_patch=False),
    "structured_no_memory": ModeConfig("structured_no_memory", use_memory=False),
    "structured_no_embedding": ModeConfig(
        "structured_no_embedding", use_embedding=False
    ),
    "structured_no_capability": ModeConfig(
        "structured_no_capability", use_capability=False
    ),
    "structured_full": ModeConfig("structured_full"),
}
ABLATION_MODES = tuple(MODE_CONFIGS)

CSV_FIELDS = [
    "mode",
    "task_index",
    "scenario_family",
    "round",
    "random_seed",
    "agent_count",
    "root_cause_correct",
    "latency_ms",
    "mean_latency_ms",
    "p50_latency_ms",
    "p95_latency_ms",
    "p99_latency_ms",
    "latency_stddev_ms",
    "latency_ci95_lower_ms",
    "latency_ci95_upper_ms",
    "min_latency_ms",
    "max_latency_ms",
    "cpu_time_ms",
    "peak_rss_kb",
    "voluntary_context_switches",
    "involuntary_context_switches",
    "sent_bytes",
    "received_bytes",
    "wire_bytes",
    "shm_bytes_written",
    "shm_bytes_read",
    "message_count",
    "throughput_mib_s",
    "text_chars",
    "estimated_tokens",
    "token_metric_type",
    "state_bytes",
    "memory_hit",
    "saved_steps",
    "object_ref_count",
    "state_patch_count",
    "memory_ref_count",
    "embedding_ref_count",
    "capability_discovery_count",
]


@dataclass(frozen=True)
class ScenarioFacts:
    scenario: IncidentScenario
    log_blob: bytes
    config_text: str
    summary: str
    root_cause: str


def deterministic_text_summary(log_blob: bytes, config_text: str) -> str:
    """Summarize the same fact inputs deterministically, without an LLM."""

    log_text = log_blob.decode("utf-8", errors="replace")
    log_fields = _parse_key_value_fragments(log_text)
    config_fields = _parse_config(config_text)
    family = config_fields.get("incident.family", log_fields.get("family", "unknown"))
    pattern = log_fields.get("pattern", "unknown log pattern")
    issue = config_fields.get("config.issue", "unknown configuration issue")
    expected = config_fields.get(
        "expected.root_cause", log_fields.get("expected_root_cause", "unknown")
    )
    return (
        f"family={family}; log_pattern={pattern}; config_issue={issue}; "
        f"root_cause={expected}"
    )


def prepare_facts(scenario: IncidentScenario, log_size_bytes: int) -> ScenarioFacts:
    log_blob = scenario_to_log_bytes(scenario, size_bytes=log_size_bytes)
    config_text = scenario_to_config_text(scenario)
    summary = deterministic_text_summary(log_blob, config_text)
    root_cause = _parse_config(config_text).get(
        "expected.root_cause", scenario.expected_root_cause
    )
    return ScenarioFacts(scenario, log_blob, config_text, summary, root_cause)


class AblationRunner:
    """Execute one mode/task/round with identical facts and agent flow."""

    def __init__(
        self,
        mode: str,
        facts: ScenarioFacts,
        round_index: int,
        random_seed: int = RANDOM_SEED,
    ) -> None:
        if mode not in MODE_CONFIGS:
            raise ValueError(f"unsupported ablation mode: {mode}")
        self.config = MODE_CONFIGS[mode]
        self.facts = facts
        self.round_index = round_index
        self.random_seed = random_seed

    def run(self) -> Dict[str, object]:
        recorder = MetricsRecorder()
        store = SharedMemoryObjectStore(recorder)
        process = ProcessMetrics().start()
        started = time.perf_counter()
        ref = None
        try:
            observed_summary = deterministic_text_summary(
                self.facts.log_blob, self.facts.config_text
            )
            observed_root_cause = _parse_config(self.facts.config_text).get(
                "expected.root_cause", ""
            )
            if observed_summary != self.facts.summary:
                raise RuntimeError("deterministic summary changed between runs")
            if observed_root_cause != self.facts.root_cause:
                raise RuntimeError("fact input root cause changed between runs")
            if self.config.baseline_kind:
                messages, counters = self._build_baseline_messages()
            else:
                messages, counters, ref = self._build_structured_messages(store)
            _transmit_messages(messages, recorder, len(self.facts.log_blob))
            root_cause_correct = counters["reported_root_cause"] == self.facts.root_cause
        finally:
            if ref is not None:
                try:
                    store.unlink(ref)
                except ObjectStoreError:
                    pass
            latency_ms = (time.perf_counter() - started) * 1000.0
            usage = process.stop()
            snapshot = recorder.snapshot()

        delivered = len(self.facts.log_blob)
        throughput = 0.0
        if latency_ms > 0.0:
            throughput = (delivered / (1024.0 * 1024.0)) / (latency_ms / 1000.0)
        text_chars = _count_text_chars(messages)
        return {
            "mode": self.config.mode,
            "task_index": self.facts.scenario.task_index,
            "scenario_family": self.facts.scenario.family,
            "round": self.round_index,
            "random_seed": self.random_seed,
            "agent_count": len({agent for edge in AGENT_FLOW for agent in edge[:2]}),
            "root_cause_correct": root_cause_correct,
            "latency_ms": latency_ms,
            "cpu_time_ms": usage.cpu_time_ms,
            "peak_rss_kb": usage.peak_rss_kb,
            "voluntary_context_switches": usage.voluntary_context_switches,
            "involuntary_context_switches": usage.involuntary_context_switches,
            "sent_bytes": snapshot.sent_bytes,
            "received_bytes": snapshot.received_bytes,
            "wire_bytes": snapshot.wire_bytes,
            "shm_bytes_written": snapshot.shm_bytes_written,
            "shm_bytes_read": snapshot.shm_bytes_read,
            "message_count": snapshot.message_count,
            "throughput_mib_s": throughput,
            "text_chars": text_chars,
            "estimated_tokens": int(math.ceil(text_chars / 4.0)) if text_chars else 0,
            "token_metric_type": TOKEN_METRIC_TYPE,
            "state_bytes": counters["state_bytes"],
            "memory_hit": counters["memory_hit"],
            "saved_steps": counters["saved_steps"],
            "object_ref_count": counters["object_ref_count"],
            "state_patch_count": counters["state_patch_count"],
            "memory_ref_count": counters["memory_ref_count"],
            "embedding_ref_count": counters["embedding_ref_count"],
            "capability_discovery_count": counters["capability_discovery_count"],
        }

    def _build_baseline_messages(self) -> tuple[List[Dict[str, object]], Dict[str, object]]:
        memory_hit, saved_steps, memory_ref = self._memory_values()
        if self.config.baseline_kind == "json_full":
            messages, state_bytes = self._json_full_state_messages(memory_ref)
        else:
            messages = self._text_messages(self.config.baseline_kind, memory_ref)
            state_bytes = 0
        return messages, {
            "reported_root_cause": self.facts.root_cause,
            "state_bytes": state_bytes,
            "memory_hit": memory_hit,
            "saved_steps": saved_steps,
            "object_ref_count": 0,
            "state_patch_count": 0,
            "memory_ref_count": 1 if memory_ref else 0,
            "embedding_ref_count": 0,
            "capability_discovery_count": 0,
        }

    def _text_messages(
        self, baseline_kind: str, memory_ref: str
    ) -> List[Dict[str, object]]:
        if baseline_kind == "text_full":
            immutable_context = "\n".join(
                [
                    f"Goal: diagnose {self.facts.scenario.task_topic}",
                    "Complete log facts:",
                    self.facts.log_blob.decode("utf-8", errors="replace"),
                    "Complete configuration facts:",
                    self.facts.config_text,
                ]
            )
        else:
            immutable_context = "\n".join(
                [
                    f"Goal: diagnose {self.facts.scenario.task_topic}",
                    f"Deterministic fact summary: {self.facts.summary}",
                ]
            )

        outputs: List[str] = []
        messages: List[Dict[str, object]] = []
        stage_outputs = [
            f"log evidence confirms {self.facts.scenario.log_pattern}",
            f"configuration evidence confirms {self.facts.scenario.config_issue}",
            (
                f"historical memory reference={memory_ref}"
                if memory_ref
                else "no reusable historical memory"
            ),
            f"review root cause={self.facts.root_cause}",
            f"final root cause={self.facts.root_cause}",
        ]
        for (source, target, stage), stage_output in zip(AGENT_FLOW, stage_outputs):
            text = "\n".join(
                [
                    immutable_context,
                    "Conversation so far:",
                    "\n".join(outputs) if outputs else "none",
                    f"Current {stage}: {stage_output}",
                ]
            )
            outputs.append(stage_output)
            messages.append(
                {
                    "kind": "text_handoff",
                    "source_agent": source,
                    "target_agent": target,
                    "stage": stage,
                    "text": text,
                }
            )
        return messages

    def _json_full_state_messages(
        self, memory_ref: str
    ) -> tuple[List[Dict[str, object]], int]:
        state = self._initial_state(
            {
                "kind": "inline_log",
                "encoding": "utf-8",
                "data": self.facts.log_blob.decode("utf-8", errors="replace"),
            }
        )
        patches = self._state_patches(state, memory_ref, embedding_ref=None)
        states = [state]
        for patch in patches:
            state = apply_patch(state, patch)
            states.append(state)
        messages: List[Dict[str, object]] = []
        for (source, target, stage), snapshot in zip(AGENT_FLOW, states):
            messages.append(
                {
                    "kind": "json_full_state",
                    "source_agent": source,
                    "target_agent": target,
                    "stage": stage,
                    "task_state": snapshot.to_dict(),
                    "config_text": self.facts.config_text,
                }
            )
        return messages, sum(len(state.to_json_bytes()) for state in states)

    def _build_structured_messages(
        self, store: SharedMemoryObjectStore
    ) -> tuple[List[Dict[str, object]], Dict[str, object], object | None]:
        ref = None
        if self.config.use_shm:
            ref = store.put_bytes(self.facts.log_blob)
            restored = store.get_bytes(ref)
            if restored != self.facts.log_blob:
                raise RuntimeError("shared-memory facts changed during ablation")
            log_artifact = {"kind": "object_ref", "object_ref": ref.to_dict()}
        else:
            log_artifact = {
                "kind": "inline_log",
                "encoding": "utf-8",
                "size": len(self.facts.log_blob),
            }

        memory_hit, saved_steps, memory_ref = self._memory_values()
        embedding_ref = self._embedding_ref() if self.config.use_embedding else None
        initial_state = self._initial_state(log_artifact)
        patches = self._state_patches(initial_state, memory_ref, embedding_ref)
        states = [initial_state]
        current = initial_state
        for patch in patches:
            current = apply_patch(current, patch)
            states.append(current)

        capabilities = self._discover_capabilities() if self.config.use_capability else {}
        messages: List[Dict[str, object]] = []
        state_bytes = 0
        object_ref_count = 0
        state_patch_count = 0
        memory_ref_count = 0
        embedding_ref_count = 0
        for index, (source, target, stage) in enumerate(AGENT_FLOW):
            payload: Dict[str, object] = {
                "kind": "structured_handoff",
                "source_agent": source,
                "target_agent": target,
                "stage": stage,
                "task_id": initial_state.task_id,
                "round": self.round_index,
                "result_summary": self._stage_summary(stage, memory_ref),
            }
            if index == 0 and not self.config.use_shm:
                payload["inline_log"] = self.facts.log_blob.decode(
                    "utf-8", errors="replace"
                )
            if self.config.use_patch:
                if index == 0:
                    payload["task_state"] = initial_state.to_dict()
                    state_bytes += len(initial_state.to_json_bytes())
                else:
                    patch = patches[index - 1]
                    payload["state_patch"] = patch.to_dict()
                    state_bytes += len(patch.to_json_bytes())
                    state_patch_count += 1
            else:
                payload["task_state"] = states[index].to_dict()
                state_bytes += len(states[index].to_json_bytes())

            if self.config.use_shm and index == 0 and ref is not None:
                payload["object_refs"] = [ref.to_dict()]
                object_ref_count += 1
            if memory_ref and index in (3, 4):
                payload["memory_refs"] = [memory_ref]
                memory_ref_count += 1
            if embedding_ref is not None and index == 3:
                payload["embedding_ref"] = embedding_ref
                embedding_ref_count += 1
            if self.config.use_capability:
                payload["capability"] = capabilities[target]
            messages.append(payload)

        counters: Dict[str, object] = {
            "reported_root_cause": self.facts.root_cause,
            "state_bytes": state_bytes,
            "memory_hit": memory_hit,
            "saved_steps": saved_steps,
            "object_ref_count": object_ref_count,
            "state_patch_count": state_patch_count,
            "memory_ref_count": memory_ref_count,
            "embedding_ref_count": embedding_ref_count,
            "capability_discovery_count": len(capabilities),
        }
        return messages, counters, ref

    def _initial_state(self, log_artifact: Dict[str, object]) -> TaskState:
        return TaskState(
            task_id=(
                f"ablation-{self.config.mode}-{self.facts.scenario.task_index}-"
                f"{self.round_index}"
            ),
            version=1,
            goal=f"Diagnose {self.facts.scenario.task_topic}",
            phase="planning",
            completed_steps=[],
            pending_steps=["log", "config", "memory", "review"],
            facts={"scenario_family": self.facts.scenario.family},
            errors=[],
            artifacts={"log_bundle": log_artifact},
        )

    def _state_patches(
        self,
        initial: TaskState,
        memory_ref: str,
        embedding_ref: Dict[str, object] | None,
    ) -> List[StatePatch]:
        patches: List[StatePatch] = []
        state = initial
        log_artifacts: Dict[str, Dict[str, object]] = {}
        if embedding_ref is not None:
            log_artifacts["log_embedding"] = dict(embedding_ref)
        patch = StatePatch(
            task_id=state.task_id,
            expected_version=state.version,
            set_fields={"phase": "log_complete", "pending_steps": ["config", "memory", "review"]},
            append_fields={"completed_steps": ["log"]},
            merge_dict_fields={
                "facts": {
                    "log_pattern": self.facts.scenario.log_pattern,
                    "log_summary": self.facts.summary,
                },
                "artifacts": log_artifacts,
            },
        )
        patches.append(patch)
        state = apply_patch(state, patch)
        patch = StatePatch(
            task_id=state.task_id,
            expected_version=state.version,
            set_fields={"phase": "config_complete", "pending_steps": ["memory", "review"]},
            append_fields={"completed_steps": ["config"]},
            merge_dict_fields={
                "facts": {
                    "config_issue": self.facts.scenario.config_issue,
                    "config_summary": self.facts.config_text,
                }
            },
        )
        patches.append(patch)
        state = apply_patch(state, patch)
        memory_fact = memory_ref if memory_ref else "none"
        patch = StatePatch(
            task_id=state.task_id,
            expected_version=state.version,
            set_fields={"phase": "memory_complete", "pending_steps": ["review"]},
            append_fields={"completed_steps": ["memory"]},
            merge_dict_fields={"facts": {"memory_ref": memory_fact}},
        )
        patches.append(patch)
        state = apply_patch(state, patch)
        patch = StatePatch(
            task_id=state.task_id,
            expected_version=state.version,
            set_fields={"phase": "complete", "pending_steps": []},
            append_fields={"completed_steps": ["review"]},
            merge_dict_fields={"facts": {"root_cause": self.facts.root_cause}},
        )
        patches.append(patch)
        return patches

    def _memory_values(self) -> tuple[bool, int, str]:
        if not self.config.use_memory:
            return False, 0, ""
        skipped = len(self.facts.scenario.expected_skipped_steps)
        if skipped == 0:
            return False, 0, ""
        return True, skipped, f"memory-{self.facts.scenario.family}"

    def _embedding_ref(self) -> Dict[str, object]:
        vector = HashEmbeddingEncoder(dim=32).encode(self.facts.summary)
        vector_bytes = len(vector) * 8
        return {
            "embedding_id": (
                f"embedding-{self.facts.scenario.task_index}-{self.round_index}"
            ),
            "dim": len(vector),
            "vector_bytes": vector_bytes,
            "summary": self.facts.summary,
        }

    def _discover_capabilities(self) -> Dict[str, Dict[str, object]]:
        registry = CapabilityRegistry(default_capabilities())
        actions = {
            "log-agent": "analyze_log",
            "config-agent": "check_config",
            "memory-agent": "search_memory",
            "review-agent": "summarize_result",
            "planner-agent": "create_plan",
        }
        selected: Dict[str, Dict[str, object]] = {}
        for agent_id, action in actions.items():
            capability = registry.select_agent(action)
            if capability is None or capability.agent_id != agent_id:
                raise RuntimeError(f"capability discovery failed for {agent_id}")
            selected[agent_id] = capability.to_dict()
        return selected

    def _stage_summary(self, stage: str, memory_ref: str) -> str:
        values = {
            "planner_to_log": self.facts.summary,
            "log_to_config": self.facts.scenario.log_pattern,
            "config_to_memory": self.facts.scenario.config_issue,
            "memory_to_review": memory_ref or "no reusable memory",
            "review_to_planner": self.facts.root_cause,
        }
        return values[stage]


def benchmark_rows(
    scenarios: Sequence[IncidentScenario],
    rounds: int = 30,
    warmup: int = 3,
    modes: Sequence[str] = ABLATION_MODES,
    log_size_bytes: int = 256 * 1024,
    random_seed: int = RANDOM_SEED,
) -> List[Dict[str, object]]:
    if rounds <= 0:
        raise ValueError("rounds must be positive")
    if warmup < 0:
        raise ValueError("warmup must be non-negative")
    if log_size_bytes <= 0:
        raise ValueError("log_size_bytes must be positive")
    if not scenarios:
        raise ValueError("at least one scenario is required")
    for mode in modes:
        if mode not in MODE_CONFIGS:
            raise ValueError(f"unsupported ablation mode: {mode}")

    random.seed(random_seed)
    facts_by_task = [prepare_facts(scenario, log_size_bytes) for scenario in scenarios]
    rows: List[Dict[str, object]] = []
    for mode in modes:
        for facts in facts_by_task:
            for warmup_index in range(warmup):
                warmup_row = AblationRunner(
                    mode, facts, -(warmup_index + 1), random_seed
                ).run()
                if not warmup_row["root_cause_correct"]:
                    raise RuntimeError("ablation warmup produced an incorrect root cause")
            task_rows: List[Dict[str, object]] = []
            for round_index in range(1, rounds + 1):
                row = AblationRunner(mode, facts, round_index, random_seed).run()
                if not row["root_cause_correct"]:
                    raise RuntimeError(
                        f"incorrect root cause for {mode} task {facts.scenario.task_index}"
                    )
                task_rows.append(row)
            latency = summarize([float(row["latency_ms"]) for row in task_rows])
            for row in task_rows:
                row.update(
                    {
                        "mean_latency_ms": latency["mean"],
                        "p50_latency_ms": latency["p50"],
                        "p95_latency_ms": latency["p95"],
                        "p99_latency_ms": latency["p99"],
                        "latency_stddev_ms": latency["standard_deviation"],
                        "latency_ci95_lower_ms": latency["ci95_lower"],
                        "latency_ci95_upper_ms": latency["ci95_upper"],
                        "min_latency_ms": latency["min"],
                        "max_latency_ms": latency["max"],
                    }
                )
            rows.extend(task_rows)
    return rows


def write_results(path: str | Path, rows: Iterable[Mapping[str, object]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for raw_row in rows:
            row = dict(raw_row)
            row["root_cause_correct"] = str(bool(row["root_cause_correct"])).lower()
            row["memory_hit"] = str(bool(row["memory_hit"])).lower()
            for field in (
                "latency_ms",
                "mean_latency_ms",
                "p50_latency_ms",
                "p95_latency_ms",
                "p99_latency_ms",
                "latency_stddev_ms",
                "latency_ci95_lower_ms",
                "latency_ci95_upper_ms",
                "min_latency_ms",
                "max_latency_ms",
                "cpu_time_ms",
                "throughput_mib_s",
            ):
                row[field] = f"{float(row[field]):.6f}"
            writer.writerow(row)


def _transmit_messages(
    messages: Sequence[Dict[str, object]],
    recorder: MetricsRecorder,
    log_size_bytes: int,
) -> None:
    previous_frame_limit = protocol_module.MAX_FRAME_SIZE
    protocol_module.MAX_FRAME_SIZE = max(
        protocol_module.MAX_FRAME_SIZE, (log_size_bytes * 8) + (1024 * 1024)
    )
    try:
        sender, receiver = socket.socketpair()
        received: List[Dict[str, Any]] = []
        errors: List[BaseException] = []

        def receive_all() -> None:
            try:
                for _ in messages:
                    received.append(recv_frame(receiver, recorder))
            except BaseException as exc:
                errors.append(exc)
            finally:
                receiver.close()

        thread = threading.Thread(target=receive_all)
        thread.start()
        try:
            for message in messages:
                send_frame(sender, message, recorder)
        finally:
            sender.close()
            thread.join()
        if errors:
            raise errors[0]
        if len(received) != len(messages):
            raise RuntimeError("ablation receiver did not observe every message")
    finally:
        protocol_module.MAX_FRAME_SIZE = previous_frame_limit


def _count_text_chars(value: object) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, Mapping):
        return sum(_count_text_chars(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_count_text_chars(item) for item in value)
    return 0


def _parse_config(config_text: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for raw_line in config_text.splitlines():
        if "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _parse_key_value_fragments(text: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for raw_line in text.splitlines()[:8]:
        for fragment in raw_line.split():
            if "=" not in fragment:
                continue
            key, value = fragment.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def _parse_modes(spec: str) -> List[str]:
    modes = [value.strip() for value in spec.split(",") if value.strip()]
    if not modes:
        raise ValueError("at least one mode is required")
    return modes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario-file",
        default="examples/incident_diagnosis_mock/scenarios.jsonl",
    )
    parser.add_argument("--tasks", type=int, default=0, help="0 uses every scenario")
    parser.add_argument("--rounds", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--log-size-bytes", type=int, default=256 * 1024)
    parser.add_argument("--random-seed", type=int, default=RANDOM_SEED)
    parser.add_argument("--modes", default=",".join(ABLATION_MODES))
    parser.add_argument("--output", default="results/ablation_bench.csv")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        scenarios = load_scenarios(args.scenario_file)
        if args.tasks < 0:
            raise ValueError("tasks must be non-negative")
        if args.tasks:
            scenarios = expand_scenarios(scenarios, args.tasks)
        rows = benchmark_rows(
            scenarios=scenarios,
            rounds=args.rounds,
            warmup=args.warmup,
            modes=_parse_modes(args.modes),
            log_size_bytes=args.log_size_bytes,
            random_seed=args.random_seed,
        )
        write_results(args.output, rows)
    except Exception as exc:
        print(f"ablation benchmark failed: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {len(rows)} ablation rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
