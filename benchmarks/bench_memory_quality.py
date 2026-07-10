#!/usr/bin/env python3
"""Evaluate memory retrieval quality against positives and hard negatives."""

from __future__ import annotations

import argparse
import csv
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

QUALITY_FAMILY_TAGS = {
    "database_timeout",
    "permission_denied",
    "storage_full",
    "cache_failure",
    "tls_failure",
}
MIN_QUERY_COUNT = 30
MIN_CORPUS_SIZE = 40


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
        _validate_dataset(memories, queries)
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
    correct_by_family: Dict[str, MemoryUnit] = {}
    stale_ids = set()

    for family in _family_specs():
        family_id = str(family["family"])
        old = _write_quality_memory(
            board,
            family_id,
            "superseded",
            family["superseded"],
            confidence=0.35,
            version=1,
            **common,
        )
        correct = _write_quality_memory(
            board,
            family_id,
            "positive",
            family["positive"],
            confidence=0.99,
            version=2,
            supersedes_memory_id=old.memory_id,
            provenance={
                "source_task_id": f"quality-{family_id}-positive",
                "source_agent": "review-agent",
                "evidence_memory_ids": [old.memory_id],
                "derivation": "corrected_after_evidence_validation",
                "recorded_at": now,
                "metadata": {"quality_family": family_id},
            },
            **common,
        )
        expired = _write_quality_memory(
            board,
            family_id,
            "expired",
            family["expired"],
            confidence=1.0,
            expires_at=now - 1.0,
            **common,
        )
        correct_by_family[family_id] = correct
        stale_ids.update({old.memory_id, expired.memory_id})

        for index, hard_negative in enumerate(family["hard_negatives"], start=1):
            _write_quality_memory(
                board,
                family_id,
                "hard_negative",
                hard_negative,
                confidence=0.94 - (index * 0.01),
                task_suffix=str(index),
                **common,
            )
        for index, distractor in enumerate(family["distractors"], start=1):
            _write_quality_memory(
                board,
                family_id,
                "distractor",
                distractor,
                confidence=0.80 - (index * 0.01),
                task_suffix=str(index),
                **common,
            )

    first_family = str(_family_specs()[0]["family"])
    duplicate = board.write_memory(
        task_id="quality-duplicate",
        source_agent="duplicate-agent",
        task_topic="dedup verification",
        memory_type="strategy",
        summary="duplicate content should not create another memory",
        content=correct_by_family[first_family].content,
        tags=["duplicate"],
        valid_from=now - 100.0,
    )

    queries: List[RetrievalQualityQuery] = []
    query_index = 0
    for family in _family_specs():
        family_id = str(family["family"])
        for query_spec in family["queries"]:
            query_index += 1
            queries.append(
                RetrievalQualityQuery(
                    query_id=f"quality-q{query_index:03d}",
                    text=str(query_spec["text"]),
                    tags=list(query_spec["tags"]),
                    relevant_memory_ids={correct_by_family[family_id].memory_id},
                    stale_memory_ids=set(stale_ids),
                )
            )

    memories = board.list_memories(active_only=False, at_time=now)
    return (
        memories,
        queries,
        duplicate.memory_id == correct_by_family[first_family].memory_id,
    )


def _write_quality_memory(
    board: SharedBlackboard,
    family: str,
    role: str,
    spec: Mapping[str, object],
    task_suffix: str = "",
    **kwargs: object,
) -> MemoryUnit:
    suffix = f"-{task_suffix}" if task_suffix else ""
    return board.write_memory(
        task_id=f"quality-{family}-{role}{suffix}",
        source_agent="review-agent",
        task_topic=str(spec["summary"]),
        memory_type="strategy",
        summary=str(spec["summary"]),
        content=str(spec["content"]),
        tags=list(spec["tags"]),
        metadata={"quality_family": family, "quality_role": role},
        **kwargs,
    )


def _validate_dataset(
    memories: Sequence[MemoryUnit],
    queries: Sequence[RetrievalQualityQuery],
) -> None:
    if len(queries) < MIN_QUERY_COUNT:
        raise RuntimeError(f"quality dataset has only {len(queries)} queries")
    if len(memories) < MIN_CORPUS_SIZE:
        raise RuntimeError(f"quality dataset has only {len(memories)} memories")
    family_counts: Dict[str, int] = {}
    hard_negative_counts: Dict[str, int] = {}
    for memory in memories:
        family = str(memory.metadata.get("quality_family", ""))
        role = str(memory.metadata.get("quality_role", ""))
        if family:
            family_counts[family] = family_counts.get(family, 0) + 1
        if family and role == "hard_negative":
            hard_negative_counts[family] = hard_negative_counts.get(family, 0) + 1
    if len(family_counts) < 5:
        raise RuntimeError("quality dataset must contain at least five families")
    if any(hard_negative_counts.get(family, 0) < 2 for family in family_counts):
        raise RuntimeError("each quality family needs at least two hard negatives")
    leaked_labels = [
        query.query_id
        for query in queries
        if QUALITY_FAMILY_TAGS & {tag.lower() for tag in query.tags}
    ]
    if leaked_labels:
        raise RuntimeError(
            f"queries expose answer family tags: {', '.join(leaked_labels)}"
        )


def _family_specs() -> List[Dict[str, object]]:
    """Return a deterministic labeled corpus without query-side family labels."""

    return [
        {
            "family": "database_timeout",
            "positive": _spec(
                "database endpoint uses a retired listener port",
                "switch database.port to the active listener after validating the endpoint",
                "database_timeout", "listener", "port_mismatch", "retired_listener", "endpoint",
            ),
            "superseded": _spec(
                "database timeout was attributed to DNS",
                "contradictory old policy says to change DNS while ignoring the listener port",
                "database_timeout", "resolver", "hostname",
            ),
            "expired": _spec(
                "retired database port compatibility workaround",
                "expired policy routes traffic through the removed compatibility listener",
                "database_timeout", "retired_listener", "compatibility",
            ),
            "hard_negatives": [
                _spec("database timeout from pool saturation", "increase the pool because endpoint and port are correct", "database_timeout", "pool", "timeout", "connection"),
                _spec("database lookup fails in the resolver", "repair DNS because the listener is reachable by address", "database_timeout", "resolver", "hostname", "timeout"),
            ],
            "distractors": [
                _spec("database replica is read only", "promote a writable replica", "database_timeout", "replica", "readonly"),
                _spec("database authentication rejected", "rotate the database password", "database_timeout", "authentication", "credential"),
                _spec("database network packet loss", "repair the lossy network path", "database_timeout", "network", "packet_loss"),
            ],
            "queries": [
                _query("connections fail after a database move while config targets the retired listener", "retired_listener", "endpoint"),
                _query("health checks show port 5433 but the service still targets 5432", "port_mismatch", "endpoint"),
                _query("the active database listener is reachable only on the replacement port", "listener", "port_mismatch"),
                _query("pool errors appeared immediately after an endpoint migration", "endpoint", "listener"),
                _query("DNS resolves and pool has headroom but sockets hit the old listener", "retired_listener", "port_mismatch"),
                _query("service configuration disagrees with the active database socket", "endpoint", "port_mismatch"),
            ],
        },
        {
            "family": "permission_denied",
            "positive": _spec(
                "credential file ownership blocks the service account",
                "restore the expected owner and restrictive mode for the runtime account",
                "permission_denied", "owner_mismatch", "file_mode", "service_account", "credential",
            ),
            "superseded": _spec("credential denial was blamed on SELinux", "contradictory old policy changes labels without checking file ownership", "permission_denied", "selinux", "label"),
            "expired": _spec("temporary world readable credential workaround", "expired policy grants broad read access to the secret", "permission_denied", "credential", "unsafe_mode"),
            "hard_negatives": [
                _spec("SELinux label denies credential access", "restore the security context while owner and mode remain correct", "permission_denied", "selinux", "label", "policy"),
                _spec("read only mount blocks credential update", "remount the volume because ownership is correct", "permission_denied", "readonly_mount", "mount", "credential"),
            ],
            "distractors": [
                _spec("expired service token", "issue a fresh token", "permission_denied", "token", "expiry"),
                _spec("missing credential path", "restore the secret mount path", "permission_denied", "missing_path", "mount"),
                _spec("directory ACL denies traversal", "repair the parent directory ACL", "permission_denied", "acl", "directory"),
            ],
            "queries": [
                _query("the runtime account cannot read a credential owned by root", "owner_mismatch", "service_account"),
                _query("secret mode and owner differ from the deployment specification", "file_mode", "owner_mismatch"),
                _query("credential becomes readable when executed as the file owner", "credential", "owner_mismatch"),
                _query("service UID changed but the mounted secret kept its old owner", "service_account", "owner_mismatch"),
                _query("security labels are valid yet the credential mode excludes the worker", "file_mode", "service_account"),
                _query("restart does not help because secret ownership remains wrong", "credential", "owner_mismatch"),
            ],
        },
        {
            "family": "storage_full",
            "positive": _spec(
                "retained WAL archives exhaust disk headroom",
                "remove obsolete WAL archives and enforce retention limits",
                "storage_full", "wal_retention", "archive", "disk_pressure", "cleanup",
            ),
            "superseded": _spec("write failures were attributed to quota", "contradictory old policy raises quota while WAL archives keep growing", "storage_full", "quota", "write"),
            "expired": _spec("disable WAL archiving during disk pressure", "expired unsafe policy disables durability to recover space", "storage_full", "wal_retention", "unsafe"),
            "hard_negatives": [
                _spec("project quota blocks storage writes", "raise project quota because the filesystem has free blocks", "storage_full", "quota", "write", "disk_pressure"),
                _spec("inode exhaustion blocks new files", "remove tiny files because block capacity remains available", "storage_full", "inode", "write", "disk_pressure"),
            ],
            "distractors": [
                _spec("snapshot reserve consumes disk", "prune obsolete snapshots", "storage_full", "snapshot", "reserve"),
                _spec("log rotation is disabled", "enable application log rotation", "storage_full", "logs", "rotation"),
                _spec("temporary upload files accumulate", "clean abandoned uploads", "storage_full", "uploads", "temporary"),
            ],
            "queries": [
                _query("archive directory grows with old WAL segments until the volume fills", "wal_retention", "archive"),
                _query("database writes resume after obsolete transaction logs are removed", "wal_retention", "cleanup"),
                _query("free space falls in step with retained recovery segments", "archive", "disk_pressure"),
                _query("quota is available but archived WAL consumes every block", "wal_retention", "disk_pressure"),
                _query("retention job stopped and recovery logs accumulated", "wal_retention", "cleanup"),
                _query("disk alarm points to the transaction archive directory", "archive", "disk_pressure"),
            ],
        },
        {
            "family": "cache_failure",
            "positive": _spec(
                "cache rejects writes under noeviction maxmemory policy",
                "select a bounded eviction policy and validate the maxmemory budget",
                "cache_failure", "noeviction", "maxmemory", "cache_write", "eviction_policy",
            ),
            "superseded": _spec("cache failures were blamed on network latency", "contradictory old policy adds retries although the server rejects writes locally", "cache_failure", "network", "latency"),
            "expired": _spec("flush the entire cache on every limit error", "expired policy destroys all keys instead of configuring eviction", "cache_failure", "flushall", "unsafe"),
            "hard_negatives": [
                _spec("cache shard hotspot raises latency", "rebalance keys because memory remains below limit", "cache_failure", "hotspot", "latency", "shard"),
                _spec("cache connection pool is exhausted", "increase client connections because writes are accepted when connected", "cache_failure", "pool", "connection", "cache_write"),
            ],
            "distractors": [
                _spec("cache replica is stale", "repair replication lag", "cache_failure", "replication", "stale"),
                _spec("cache keys expire too early", "adjust key TTL", "cache_failure", "ttl", "expiry"),
                _spec("cache authentication fails", "rotate the cache credential", "cache_failure", "authentication", "credential"),
            ],
            "queries": [
                _query("writes return maxmemory errors while reads still succeed", "maxmemory", "cache_write"),
                _query("the server reports no keys can be evicted under the current policy", "noeviction", "eviction_policy"),
                _query("memory reaches its cap and new cache entries are rejected", "maxmemory", "noeviction"),
                _query("latency is normal but SET commands fail at the configured limit", "cache_write", "maxmemory"),
                _query("capacity alarm coincides with a noeviction configuration", "noeviction", "eviction_policy"),
                _query("changing the eviction mode restores bounded cache writes", "eviction_policy", "cache_write"),
            ],
        },
        {
            "family": "tls_failure",
            "positive": _spec(
                "expired server certificate breaks the TLS handshake",
                "rotate the certificate before notAfter and reload the serving process",
                "tls_failure", "certificate_expiry", "not_after", "handshake", "rotation",
            ),
            "superseded": _spec("TLS failure was blamed on hostname mismatch", "contradictory old policy changes DNS although the certificate names are correct", "tls_failure", "hostname", "san"),
            "expired": _spec("disable certificate validation temporarily", "expired unsafe policy bypasses verification instead of rotating the certificate", "tls_failure", "insecure_skip_verify", "unsafe"),
            "hard_negatives": [
                _spec("TLS trust chain is incomplete", "install the intermediate CA while the leaf remains valid", "tls_failure", "trust_chain", "intermediate", "handshake"),
                _spec("TLS hostname does not match SAN", "issue a certificate containing the service name before expiry", "tls_failure", "hostname", "san", "handshake"),
            ],
            "distractors": [
                _spec("TLS protocol version is unsupported", "align minimum protocol versions", "tls_failure", "protocol", "version"),
                _spec("TLS cipher suites do not overlap", "configure a shared cipher", "tls_failure", "cipher", "negotiation"),
                _spec("client clock is far ahead", "repair clock synchronization", "tls_failure", "clock", "time"),
            ],
            "queries": [
                _query("handshakes started failing immediately after certificate notAfter", "certificate_expiry", "not_after"),
                _query("the served leaf certificate is past its validity end", "certificate_expiry", "rotation"),
                _query("hostname and chain validate but the certificate date is no longer valid", "not_after", "certificate_expiry"),
                _query("reloading a renewed certificate restores the handshake", "rotation", "handshake"),
                _query("monitoring shows the endpoint certificate crossed its expiry deadline", "certificate_expiry", "not_after"),
                _query("TLS negotiation fails with an expired-certificate alert", "handshake", "certificate_expiry"),
            ],
        },
    ]


def _spec(summary: str, content: str, *tags: str) -> Dict[str, object]:
    return {"summary": summary, "content": content, "tags": list(tags)}


def _query(text: str, *tags: str) -> Dict[str, object]:
    return {"text": text, "tags": list(tags)}


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
