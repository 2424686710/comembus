#!/usr/bin/env python3
"""Evaluate memory retrieval quality against positives and hard negatives."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
import sys
import time
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comembus.memory.blackboard import SharedBlackboard
from comembus.memory.quality import RetrievalQualityQuery, evaluate_retrieval_quality
from comembus.memory.ranking import RANKING_METHODS, MemoryRanker
from comembus.memory.unit import MemoryUnit


CSV_FIELDS = [
    "method",
    "query_count",
    "corpus_size",
    "dedup_verified",
    "precision_at_1",
    "precision_at_3",
    "recall_at_1",
    "recall_at_3",
    "mrr",
    "wrong_reuse_rate",
    "stale_memory_rejection_rate",
    "query_latency_ms",
    "task_success_rate",
]


def benchmark_rows(db_path: str) -> List[Dict[str, object]]:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    board = SharedBlackboard(str(path), embedding_dim=128)
    evaluation_time = time.time()
    try:
        memories, queries, dedup_verified = _build_quality_corpus(
            board, evaluation_time
        )
        ranker = MemoryRanker(embedding_dim=128)
        rows: List[Dict[str, object]] = []
        for method in RANKING_METHODS:
            row = evaluate_retrieval_quality(
                method,
                queries,
                memories,
                ranker=ranker,
                at_time=evaluation_time,
            )
            row["corpus_size"] = len(memories)
            row["dedup_verified"] = dedup_verified
            rows.append(row)
        hybrid = next(row for row in rows if row["method"] == "hybrid")
        best_single = max(
            float(row["mrr"]) for row in rows if row["method"] != "hybrid"
        )
        if float(hybrid["mrr"]) + 0.01 < best_single:
            raise RuntimeError(
                f"hybrid MRR regression: {hybrid['mrr']} < {best_single}"
            )
        if any(float(row["stale_memory_rejection_rate"]) < 1.0 for row in rows):
            raise RuntimeError("expired or superseded memory entered retrieval results")
        return rows
    finally:
        board.close()


def _build_quality_corpus(
    board: SharedBlackboard,
    now: float,
) -> Tuple[List[MemoryUnit], List[RetrievalQualityQuery], bool]:
    common = {"valid_from": now - 100.0}
    db_old = board.write_memory(
        task_id="db-old",
        source_agent="review-agent",
        task_topic="database timeout",
        memory_type="strategy",
        summary="database timeout was caused by DNS",
        content="contradictory old diagnosis: change DNS and ignore the database port",
        tags=["database_timeout", "wrong_port", "database"],
        confidence=0.4,
        version=1,
        **common,
    )
    db_correct = board.write_memory(
        task_id="db-good",
        source_agent="review-agent",
        task_topic="database timeout wrong port",
        memory_type="strategy",
        summary="database timeout caused by wrong port on obsolete listener",
        content="validate database.port then switch to the active listener",
        tags=["database_timeout", "wrong_port", "database"],
        confidence=0.99,
        version=2,
        supersedes_memory_id=db_old.memory_id,
        provenance={
            "source_task_id": "db-good",
            "source_agent": "review-agent",
            "evidence_memory_ids": [db_old.memory_id],
            "derivation": "corrected_after_config_validation",
            "recorded_at": now,
            "metadata": {"root_cause": "wrong_port"},
        },
        **common,
    )
    duplicate = board.write_memory(
        task_id="db-duplicate",
        source_agent="another-agent",
        task_topic="duplicate",
        memory_type="strategy",
        summary="same content should deduplicate",
        content="validate database.port then switch to the active listener",
        tags=["duplicate"],
        **common,
    )
    db_expired = board.write_memory(
        task_id="db-expired",
        source_agent="review-agent",
        task_topic="database timeout old policy",
        memory_type="strategy",
        summary="old wrong-port recovery policy",
        content="expired strategy tells workers to use the retired port 15432",
        tags=["database_timeout", "wrong_port", "database"],
        confidence=1.0,
        expires_at=now - 1.0,
        **common,
    )
    db_hard = board.write_memory(
        task_id="db-hard",
        source_agent="log-agent",
        task_topic="database timeout connection pool",
        memory_type="strategy",
        summary="database timeout connection pool failure root cause",
        content="hard negative: increase pool size; the port and listener are correct",
        tags=["database_timeout", "pool", "database"],
        confidence=0.95,
        **common,
    )
    permission_correct = board.write_memory(
        task_id="permission-good",
        source_agent="review-agent",
        task_topic="credential permission denied",
        memory_type="strategy",
        summary="credential permission denied from file ownership mismatch",
        content="fix credential owner and mode for the runtime service account",
        tags=["permission_denied", "credentials", "ownership"],
        confidence=0.99,
        **common,
    )
    permission_hard = board.write_memory(
        task_id="permission-hard",
        source_agent="review-agent",
        task_topic="permission denied SELinux",
        memory_type="strategy",
        summary="permission denied from SELinux policy",
        content="hard negative: adjust SELinux label, file ownership is already correct",
        tags=["permission_denied", "selinux"],
        confidence=0.92,
        **common,
    )
    storage_correct = board.write_memory(
        task_id="storage-good",
        source_agent="review-agent",
        task_topic="storage full WAL",
        memory_type="strategy",
        summary="storage full because retained WAL exhausted the disk",
        content="remove stale WAL archives and restore disk headroom",
        tags=["storage_full", "wal", "disk"],
        confidence=0.99,
        **common,
    )
    storage_hard = board.write_memory(
        task_id="storage-hard",
        source_agent="review-agent",
        task_topic="storage quota",
        memory_type="strategy",
        summary="storage write failure from project quota",
        content="hard negative: disk has space but a project quota blocks writes",
        tags=["storage_full", "quota", "disk"],
        confidence=0.9,
        **common,
    )

    memories = board.list_memories(active_only=False, at_time=now)
    stale = {db_old.memory_id, db_expired.memory_id}
    queries = [
        RetrievalQualityQuery(
            "db-specific",
            "database timeout obsolete listener wrong port",
            ["database_timeout", "wrong_port"],
            {db_correct.memory_id},
            stale,
        ),
        RetrievalQualityQuery(
            "db-hard-negative",
            "database timeout connection pool symptoms but configuration wrong port root cause",
            ["database_timeout", "wrong_port"],
            {db_correct.memory_id},
            stale,
        ),
        RetrievalQualityQuery(
            "permission-specific",
            "credential file permission denied owner mismatch",
            ["permission_denied", "credentials"],
            {permission_correct.memory_id},
            stale,
        ),
        RetrievalQualityQuery(
            "permission-hard-negative",
            "permission denied service account cannot read credential",
            ["permission_denied", "ownership"],
            {permission_correct.memory_id},
            stale,
        ),
        RetrievalQualityQuery(
            "storage-specific",
            "database storage full retained WAL disk",
            ["storage_full", "wal"],
            {storage_correct.memory_id},
            stale,
        ),
        RetrievalQualityQuery(
            "storage-hard-negative",
            "storage write failure disk full versus quota",
            ["storage_full", "wal"],
            {storage_correct.memory_id},
            stale,
        ),
    ]
    return memories, queries, duplicate.memory_id == db_correct.memory_id


def write_results(path: str | Path, rows: Iterable[Mapping[str, object]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for source in rows:
            row = dict(source)
            row["dedup_verified"] = str(bool(row["dedup_verified"])).lower()
            for field in CSV_FIELDS[4:]:
                row[field] = f"{float(row[field]):.6f}"
            writer.writerow(row)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="results/memory_quality.csv")
    parser.add_argument("--db-path", default="results/memory_quality.sqlite")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        rows = benchmark_rows(args.db_path)
        write_results(args.output, rows)
    except Exception as exc:
        print(f"memory quality benchmark failed: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {len(rows)} memory quality rows to {args.output}")
    for row in rows:
        print(
            f"{row['method']}: MRR={float(row['mrr']):.4f} "
            f"wrong_reuse_rate={float(row['wrong_reuse_rate']):.4f} "
            f"stale_rejection={float(row['stale_memory_rejection_rate']):.4f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
