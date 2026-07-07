#!/usr/bin/env python3
"""Benchmark structured memory reuse across related tasks."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
import sys
import time
from typing import Iterable, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comembus.memory.blackboard import SharedBlackboard
from examples.incident_diagnosis_mock.scenarios import (
    IncidentScenario,
    default_scenarios,
    expand_scenarios,
    load_scenarios,
)

CSV_FIELDS = [
    "task_index",
    "task_topic",
    "memory_hit",
    "reused_memory_id",
    "baseline_steps",
    "structured_steps",
    "saved_steps",
    "query_latency_ms",
    "total_latency_ms",
    "returned_memories",
]


@dataclass(frozen=True)
class MemoryReuseRow:
    task_index: int
    task_topic: str
    memory_hit: bool
    reused_memory_id: str
    baseline_steps: int
    structured_steps: int
    saved_steps: int
    query_latency_ms: float
    total_latency_ms: float
    returned_memories: int

    def to_csv_row(self) -> dict[str, object]:
        return {
            "task_index": self.task_index,
            "task_topic": self.task_topic,
            "memory_hit": str(self.memory_hit).lower(),
            "reused_memory_id": self.reused_memory_id,
            "baseline_steps": self.baseline_steps,
            "structured_steps": self.structured_steps,
            "saved_steps": self.saved_steps,
            "query_latency_ms": f"{self.query_latency_ms:.3f}",
            "total_latency_ms": f"{self.total_latency_ms:.3f}",
            "returned_memories": self.returned_memories,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=10, help="Number of sequential tasks")
    parser.add_argument(
        "--output",
        default="results/memory_reuse_bench.csv",
        help="CSV output path",
    )
    parser.add_argument(
        "--db-path",
        default="results/memory_reuse_bench.sqlite",
        help="SQLite database path",
    )
    parser.add_argument(
        "--scenario-file",
        default="",
        help="Optional JSONL scenario file. Defaults to built-in rich scenarios.",
    )
    return parser.parse_args()


def benchmark_rows(
    task_count: int,
    db_path: str,
    scenario_file: str = "",
) -> List[MemoryReuseRow]:
    if task_count <= 0:
        raise ValueError("tasks must be positive")

    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    if db_file.exists():
        db_file.unlink()

    board = SharedBlackboard(str(db_file))
    baseline_steps = 5
    rows: List[MemoryReuseRow] = []
    scenarios = _benchmark_scenarios(task_count, scenario_file)
    seen_families: set[str] = set()
    try:
        for scenario in scenarios:
            started = time.perf_counter()

            if scenario.family not in seen_families:
                seen_families.add(scenario.family)
                query_latency_ms = 0.0
                total_latency_ms = (time.perf_counter() - started) * 1000.0
                rows.append(
                    MemoryReuseRow(
                        task_index=scenario.task_index,
                        task_topic=scenario.task_topic,
                        memory_hit=False,
                        reused_memory_id="",
                        baseline_steps=baseline_steps,
                        structured_steps=baseline_steps,
                        saved_steps=0,
                        query_latency_ms=query_latency_ms,
                        total_latency_ms=total_latency_ms,
                        returned_memories=0,
                    )
                )
                _write_follow_up_memories(board, scenario, reused_memory_id="")
                continue

            query_started = time.perf_counter()
            hits = board.search(
                scenario.related_memory_query,
                tags=scenario.tags,
                top_k=3,
            )
            query_latency_ms = (time.perf_counter() - query_started) * 1000.0
            memory_hit = bool(hits)
            reused_memory_id = hits[0].memory.memory_id if hits else ""
            structured_steps = (
                max(1, baseline_steps - len(scenario.expected_skipped_steps))
                if memory_hit
                else baseline_steps
            )
            saved_steps = baseline_steps - structured_steps
            total_latency_ms = (time.perf_counter() - started) * 1000.0

            rows.append(
                MemoryReuseRow(
                    task_index=scenario.task_index,
                    task_topic=scenario.task_topic,
                    memory_hit=memory_hit,
                    reused_memory_id=reused_memory_id,
                    baseline_steps=baseline_steps,
                    structured_steps=structured_steps,
                    saved_steps=saved_steps,
                    query_latency_ms=query_latency_ms,
                    total_latency_ms=total_latency_ms,
                    returned_memories=len(hits),
                )
            )
            _write_follow_up_memories(board, scenario, reused_memory_id)
        return rows
    finally:
        board.close()


def _write_follow_up_memories(
    board: SharedBlackboard,
    scenario: IncidentScenario,
    reused_memory_id: str,
) -> None:
    board.write_memory(
        task_id=f"task-{scenario.task_index}",
        source_agent="log-agent",
        task_topic=scenario.task_topic,
        memory_type="evidence",
        summary=f"{scenario.family} log evidence for task {scenario.task_index}",
        content=(
            f"log_pattern={scenario.log_pattern}; "
            f"related_memory_query={scenario.related_memory_query}; "
            f"expected_root_cause={scenario.expected_root_cause}"
        ),
        tags=list(scenario.tags),
        confidence=0.94,
        metadata={"family": scenario.family, "reused_memory_id": reused_memory_id},
    )
    board.write_memory(
        task_id=f"task-{scenario.task_index}",
        source_agent="review-agent",
        task_topic=scenario.task_topic,
        memory_type="strategy",
        summary=f"{scenario.family} reuse strategy",
        content=(
            "reuse prior diagnosis and skip repeated steps"
            if reused_memory_id
            else "establish the first family memory for later reuse"
        ),
        tags=list(scenario.tags) + ["strategy"],
        confidence=0.9,
        metadata={
            "reused_memory_id": reused_memory_id,
            "expected_skipped_steps": list(scenario.expected_skipped_steps),
        },
    )


def _benchmark_scenarios(task_count: int, scenario_file: str) -> List[IncidentScenario]:
    scenarios = load_scenarios(scenario_file) if scenario_file else default_scenarios()
    return expand_scenarios(scenarios, task_count)


def write_results(path: str, rows: Iterable[MemoryReuseRow]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_csv_row())


def print_summary(path: str, rows: List[MemoryReuseRow]) -> None:
    memory_hit_count = sum(1 for row in rows if row.memory_hit)
    memory_hit_rate = (memory_hit_count / len(rows)) if rows else 0.0
    print(f"wrote {len(rows)} benchmark rows to {path}")
    print(f"memory_hit_count={memory_hit_count}")
    print(f"memory_hit_rate={memory_hit_rate:.4f}")


def main() -> int:
    args = parse_args()
    try:
        rows = benchmark_rows(
            task_count=args.tasks,
            db_path=args.db_path,
            scenario_file=args.scenario_file,
        )
    except Exception as exc:
        print(f"memory reuse benchmark failed: {exc}", file=sys.stderr)
        return 1

    write_results(args.output, rows)
    print_summary(args.output, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
