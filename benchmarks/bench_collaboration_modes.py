#!/usr/bin/env python3
"""Benchmark text_mode versus structured_mode across related tasks."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys
from typing import Iterable, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comembus.collab.metrics import CollaborationMetrics
from comembus.collab.structured_mode import StructuredCollaborationRunner
from comembus.collab.text_mode import TextCollaborationRunner
from examples.incident_diagnosis_mock.scenarios import (
    IncidentScenario,
    default_scenarios,
    expand_scenarios,
    load_scenarios,
)

CSV_FIELDS = [
    "mode",
    "task_index",
    "task_topic",
    "message_count",
    "text_chars",
    "approx_tokens",
    "protocol_bytes",
    "object_ref_count",
    "state_patch_count",
    "memory_ref_count",
    "non_text_state_bytes",
    "shared_object_bytes",
    "memory_hit",
    "reused_memory_id",
    "baseline_steps",
    "actual_steps",
    "saved_steps",
    "total_latency_ms",
    "root_cause_correct",
    "scenario_family",
    "capability_count",
    "capability_discovery_count",
    "embedding_state_count",
    "embedding_state_bytes",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=10, help="Number of related tasks")
    parser.add_argument(
        "--text-context-bytes",
        type=int,
        default=65536,
        help="Approximate text context size for text_mode messages",
    )
    parser.add_argument(
        "--output",
        default="results/collaboration_bench.csv",
        help="CSV output path",
    )
    parser.add_argument(
        "--db-path",
        default="results/collaboration_bench.sqlite",
        help="SQLite database path for structured_mode memory reuse",
    )
    parser.add_argument(
        "--scenario-file",
        default="",
        help="Optional JSONL scenario file. Defaults to built-in rich scenarios.",
    )
    return parser.parse_args()


def benchmark_rows(
    task_count: int,
    text_context_bytes: int,
    db_path: str,
    scenario_file: str = "",
) -> List[CollaborationMetrics]:
    if task_count <= 0:
        raise ValueError("tasks must be positive")
    if text_context_bytes <= 0:
        raise ValueError("text-context-bytes must be positive")

    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    if db_file.exists():
        db_file.unlink()

    rows: List[CollaborationMetrics] = []
    scenarios = _benchmark_scenarios(task_count, scenario_file)
    for scenario in scenarios:
        task_index = scenario.task_index
        task_topic = scenario.task_topic

        text_metrics = TextCollaborationRunner(
            task_index=task_index,
            task_topic=task_topic,
            scenario=scenario,
            text_context_bytes=text_context_bytes,
        ).run()
        rows.append(text_metrics)

        structured_metrics = StructuredCollaborationRunner(
            task_index=task_index,
            task_topic=task_topic,
            db_path=str(db_file),
            scenario=scenario,
        ).run()
        rows.append(structured_metrics)
    return rows


def write_results(path: str, rows: Iterable[CollaborationMetrics]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(_csv_row(row))


def _csv_row(row: CollaborationMetrics) -> dict[str, object]:
    payload = row.to_dict()
    payload["memory_hit"] = str(bool(payload["memory_hit"])).lower()
    payload["root_cause_correct"] = str(bool(payload["root_cause_correct"])).lower()
    payload["total_latency_ms"] = f"{float(payload['total_latency_ms']):.3f}"
    return payload


def print_summary(path: str, rows: List[CollaborationMetrics]) -> None:
    print(f"wrote {len(rows)} benchmark rows to {path}")


def _benchmark_scenarios(task_count: int, scenario_file: str) -> List[IncidentScenario]:
    scenarios = load_scenarios(scenario_file) if scenario_file else default_scenarios()
    return expand_scenarios(scenarios, task_count)


def main() -> int:
    args = parse_args()
    try:
        rows = benchmark_rows(
            task_count=args.tasks,
            text_context_bytes=args.text_context_bytes,
            db_path=args.db_path,
            scenario_file=args.scenario_file,
        )
    except Exception as exc:
        print(f"collaboration benchmark failed: {exc}", file=sys.stderr)
        return 1

    write_results(args.output, rows)
    print_summary(args.output, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
