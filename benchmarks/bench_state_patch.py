#!/usr/bin/env python3
"""Benchmark full task state handoff versus incremental StatePatch handoff."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Iterable, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comembus.state.manager import InMemoryStateManager
from comembus.state.patch import StatePatch
from comembus.state.task_state import TaskState

STATE_FACT_COUNTS = {
    "small": 10,
    "medium": 100,
    "large": 1000,
}

CSV_FIELDS = [
    "mode",
    "state_size",
    "full_state_bytes",
    "patch_bytes",
    "reduction_ratio",
    "version_ok",
]


@dataclass(frozen=True)
class StateBenchmarkRow:
    mode: str
    state_size: str
    full_state_bytes: int
    patch_bytes: int
    reduction_ratio: float
    version_ok: bool

    def to_csv_row(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "state_size": self.state_size,
            "full_state_bytes": self.full_state_bytes,
            "patch_bytes": self.patch_bytes,
            "reduction_ratio": f"{self.reduction_ratio:.6f}",
            "version_ok": str(self.version_ok).lower(),
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="results/state_patch_bench.csv",
        help="CSV output path",
    )
    return parser.parse_args()


def build_state(state_size: str, fact_count: int) -> TaskState:
    facts = {
        f"fact_{index:04d}": (
            f"checkout-observation-{index:04d}-database-pool-pressure-signal"
        )
        for index in range(fact_count)
    }
    return TaskState(
        task_id=f"task-{state_size}",
        version=1,
        goal="Diagnose checkout saturation without resending the full state",
        phase="collecting",
        completed_steps=["open_incident"],
        pending_steps=["analyze_logs", "review_config", "write_rca"],
        facts=facts,
        errors=[],
        artifacts={
            "log_bundle": {
                "kind": "object_ref",
                "size_bytes": 8 * 1024 * 1024,
            },
            "config_snapshot": {
                "kind": "inline_text",
                "line_count": 6,
            },
        },
    )


def build_patch(state: TaskState) -> StatePatch:
    return StatePatch(
        task_id=state.task_id,
        expected_version=state.version,
        set_fields={"phase": "reviewing"},
        append_fields={"completed_steps": ["review_combined_evidence"]},
        merge_dict_fields={"facts": {"latest_signal": "database_pool_warn"}},
    )


def benchmark_rows() -> List[StateBenchmarkRow]:
    rows: List[StateBenchmarkRow] = []
    for state_size, fact_count in STATE_FACT_COUNTS.items():
        manager = InMemoryStateManager()
        state = manager.create_state(build_state(state_size, fact_count))
        patch = build_patch(state)
        updated = manager.apply_patch(patch)

        full_state_bytes = len(updated.to_json_bytes())
        patch_bytes = len(patch.to_json_bytes())
        reduction_ratio = patch_bytes / full_state_bytes
        version_ok = (
            updated.version == state.version + 1
            and updated.phase == "reviewing"
            and updated.completed_steps[-1] == "review_combined_evidence"
            and updated.facts.get("latest_signal") == "database_pool_warn"
        )

        rows.append(
            StateBenchmarkRow(
                mode="full_state",
                state_size=state_size,
                full_state_bytes=full_state_bytes,
                patch_bytes=patch_bytes,
                reduction_ratio=1.0,
                version_ok=version_ok,
            )
        )
        rows.append(
            StateBenchmarkRow(
                mode="patch",
                state_size=state_size,
                full_state_bytes=full_state_bytes,
                patch_bytes=patch_bytes,
                reduction_ratio=reduction_ratio,
                version_ok=version_ok,
            )
        )
    return rows


def write_results(path: str, rows: Iterable[StateBenchmarkRow]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_csv_row())


def print_summary(path: str, rows: List[StateBenchmarkRow]) -> None:
    print(f"wrote {len(rows)} benchmark rows to {path}")
    for row in rows:
        if row.mode == "patch":
            print(
                f"{row.state_size}: full_state_bytes={row.full_state_bytes} "
                f"patch_bytes={row.patch_bytes} "
                f"reduction_ratio={row.reduction_ratio:.6f} "
                f"version_ok={str(row.version_ok).lower()}"
            )


def main() -> int:
    args = parse_args()
    try:
        rows = benchmark_rows()
    except Exception as exc:
        print(f"state patch benchmark failed: {exc}", file=sys.stderr)
        return 1

    write_results(args.output, rows)
    print_summary(args.output, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

