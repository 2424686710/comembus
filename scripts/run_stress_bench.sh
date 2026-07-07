#!/usr/bin/env bash
set -euo pipefail

mkdir -p results/stress

python3 benchmarks/bench_collaboration_modes.py \
  --tasks 30 \
  --text-context-bytes 16384 \
  --output results/stress/collaboration_30_16k.csv \
  --db-path results/stress/collaboration_30_16k.sqlite

python3 benchmarks/bench_collaboration_modes.py \
  --tasks 30 \
  --text-context-bytes 65536 \
  --output results/stress/collaboration_30_64k.csv \
  --db-path results/stress/collaboration_30_64k.sqlite

python3 benchmarks/bench_collaboration_modes.py \
  --tasks 60 \
  --text-context-bytes 65536 \
  --output results/stress/collaboration_60_64k.csv \
  --db-path results/stress/collaboration_60_64k.sqlite

for path in \
  results/stress/collaboration_30_16k.csv \
  results/stress/collaboration_30_64k.csv \
  results/stress/collaboration_60_64k.csv
do
  python3 - "$path" <<'PY'
import csv
import sys

path = sys.argv[1]
with open(path, newline="", encoding="utf-8") as handle:
    rows = list(csv.DictReader(handle))

text_rows = [row for row in rows if row["mode"] == "text_mode"]
structured_rows = [row for row in rows if row["mode"] == "structured_mode"]

text_total_tokens = sum(int(row["approx_tokens"]) for row in text_rows)
structured_total_tokens = sum(int(row["approx_tokens"]) for row in structured_rows)
text_total_latency = sum(float(row["total_latency_ms"]) for row in text_rows)
structured_total_latency = sum(float(row["total_latency_ms"]) for row in structured_rows)
token_saving_ratio = (
    (text_total_tokens - structured_total_tokens) / text_total_tokens
    if text_total_tokens else 0.0
)
latency_saving_ratio = (
    (text_total_latency - structured_total_latency) / text_total_latency
    if text_total_latency else 0.0
)
memory_hit_rate = (
    sum(1 for row in structured_rows if row["memory_hit"] == "true") / len(structured_rows)
    if structured_rows else 0.0
)
root_cause_all_true = all(row["root_cause_correct"] == "true" for row in rows)

print(f"path={path}")
print(f"row_count={len(rows)}")
print(f"text_mode total_tokens={text_total_tokens}")
print(f"structured_mode total_tokens={structured_total_tokens}")
print(f"token_saving_ratio={token_saving_ratio:.6f}")
print(f"latency_saving_ratio={latency_saving_ratio:.6f}")
print(f"structured_mode memory_hit_rate={memory_hit_rate:.4f}")
print(f"root_cause_correct_all_true={str(root_cause_all_true).lower()}")
PY
done

if [ -d /dev/shm ]; then
  leftover="$(find /dev/shm -maxdepth 1 -name 'comembus_*' -print)"
  if [ -n "${leftover}" ]; then
    echo "warning: leftover shared memory objects detected"
    echo "${leftover}"
  fi
fi
