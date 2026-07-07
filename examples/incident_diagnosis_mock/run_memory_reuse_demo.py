#!/usr/bin/env python3
"""Demonstrate cross-task memory reuse with the shared blackboard."""

from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comembus.memory.blackboard import SharedBlackboard


def main() -> int:
    results_dir = ROOT / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    db_path = results_dir / "comembus_memory_demo.sqlite"
    if db_path.exists():
        db_path.unlink()

    board = SharedBlackboard(str(db_path))
    try:
        task_1_topic = "database connection timeout"
        evidence = board.write_memory(
            task_id="task-1",
            source_agent="log-agent",
            task_topic=task_1_topic,
            memory_type="evidence",
            summary="database timeout observed in logs",
            content="log_error=database timeout while checkout-api connects to database",
            tags=["database", "timeout", "incident"],
            confidence=0.95,
            metadata={"step": "log_analysis"},
        )
        summary = board.write_memory(
            task_id="task-1",
            source_agent="config-agent",
            task_topic=task_1_topic,
            memory_type="summary",
            summary="wrong database port triggered the timeout",
            content="config_port=wrong database port and root_cause=wrong database port caused database timeout",
            tags=["database", "port", "root_cause"],
            confidence=0.92,
            metadata={"step": "config_check"},
        )
        strategy = board.write_memory(
            task_id="task-1",
            source_agent="review-agent",
            task_topic=task_1_topic,
            memory_type="strategy",
            summary="check database port before full log scan",
            content="When database timeout and wrong port signals appear together, validate the database port first and skip a full_log_scan if confirmed.",
            tags=["database", "port", "strategy"],
            confidence=0.97,
            metadata={"step": "review"},
        )
        print(
            f"Task 1 wrote memories: {[evidence.memory_id, summary.memory_id, strategy.memory_id]}",
            flush=True,
        )

        task_2_topic = "similar database connection failure"
        hits = board.search("database timeout wrong port", tags=["database", "port"], top_k=3)
        memory_hit = bool(hits)
        reused_memory_id = hits[0].memory.memory_id if hits else ""
        skipped_steps = ["full_log_scan"] if memory_hit else []
        final_root_cause = (
            "wrong database port caused database timeout"
            if memory_hit
            else "root cause unresolved"
        )

        print(f"Task 2 memory hit={memory_hit}", flush=True)
        print(f"reused_memory_id={reused_memory_id}", flush=True)
        print(f"skipped_steps={skipped_steps}", flush=True)
        print(f"final root cause={final_root_cause}", flush=True)
        print("OK: memory reuse demo completed")
        return 0
    finally:
        board.close()


if __name__ == "__main__":
    raise SystemExit(main())
