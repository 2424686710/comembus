#!/usr/bin/env python3
"""Run a single-task comparison between text_mode and structured_mode."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comembus.collab.structured_mode import StructuredCollaborationRunner
from comembus.collab.text_mode import TextCollaborationRunner


def main() -> int:
    results_dir = ROOT / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    db_path = results_dir / "collaboration_demo.sqlite"
    if db_path.exists():
        db_path.unlink()

    task_topic = "database connection timeout incident"
    text_metrics = TextCollaborationRunner(
        task_index=1,
        task_topic=task_topic,
        text_context_bytes=65536,
    ).run()
    structured_metrics = StructuredCollaborationRunner(
        task_index=1,
        task_topic=task_topic,
        db_path=str(db_path),
    ).run()

    if text_metrics.approx_tokens == 0:
        token_saving_ratio = 0.0
    else:
        token_saving_ratio = (
            (text_metrics.approx_tokens - structured_metrics.approx_tokens)
            / float(text_metrics.approx_tokens)
        )

    root_cause_correct = (
        text_metrics.root_cause_correct and structured_metrics.root_cause_correct
    )

    print(f"text_mode approx_tokens={text_metrics.approx_tokens}")
    print(f"structured_mode approx_tokens={structured_metrics.approx_tokens}")
    print(f"text_mode text_chars={text_metrics.text_chars}")
    print(f"structured_mode text_chars={structured_metrics.text_chars}")
    print(f"token_saving_ratio={token_saving_ratio:.6f}")
    print(f"root_cause_correct={str(root_cause_correct).lower()}")
    print("OK: collaboration modes demo completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

