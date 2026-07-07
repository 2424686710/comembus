#!/usr/bin/env bash
set -euo pipefail

mkdir -p results

python3 benchmarks/bench_memory_reuse.py \
  --tasks 10 \
  --output results/memory_reuse_bench.csv \
  --db-path results/memory_reuse_bench.sqlite \
  --scenario-file examples/incident_diagnosis_mock/scenarios.jsonl

sed -n '1,20p' results/memory_reuse_bench.csv

python3 - <<'PY'
import csv
from pathlib import Path

path = Path("results/memory_reuse_bench.csv")
with path.open(newline="", encoding="utf-8") as handle:
    rows = list(csv.DictReader(handle))

memory_hit_count = sum(1 for row in rows if row["memory_hit"] == "true")
memory_hit_rate = (memory_hit_count / len(rows)) if rows else 0.0

print(f"memory_hit_count={memory_hit_count}")
print(f"memory_hit_rate={memory_hit_rate:.4f}")
PY
