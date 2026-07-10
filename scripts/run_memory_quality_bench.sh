#!/usr/bin/env bash
set -euo pipefail

mkdir -p results

db_path="$(mktemp /tmp/comembus-memory-quality.XXXXXX.sqlite)"
trap 'rm -f "${db_path}" "${db_path}-wal" "${db_path}-shm"' EXIT

python3 benchmarks/bench_memory_quality.py \
  --output results/memory_quality.csv \
  --db-path "${db_path}"

python3 - <<'PY'
import csv
rows = list(csv.DictReader(open("results/memory_quality.csv", newline="", encoding="utf-8")))
by_method = {row["method"]: row for row in rows}
required = {"keyword_only", "tag_only", "hash_embedding_only", "hybrid"}
if set(by_method) != required:
    raise SystemExit("memory quality methods are incomplete")
hybrid_mrr = float(by_method["hybrid"]["mrr"])
best_single = max(float(by_method[name]["mrr"]) for name in required - {"hybrid"})
if hybrid_mrr + 0.01 < best_single:
    raise SystemExit("hybrid MRR regressed beyond tolerance")
if not all(float(row["stale_memory_rejection_rate"]) == 1.0 for row in rows):
    raise SystemExit("stale memory entered reuse results")
if not all("wrong_reuse_rate" in row for row in rows):
    raise SystemExit("wrong_reuse_rate is missing")
print("memory quality acceptance checks passed")
PY
