#!/usr/bin/env bash
set -euo pipefail

mkdir -p results

python3 benchmarks/bench_collaboration_modes.py \
  --tasks 10 \
  --text-context-bytes 65536 \
  --output results/collaboration_bench.csv \
  --db-path results/collaboration_bench.sqlite \
  --scenario-file examples/incident_diagnosis_mock/scenarios.jsonl

sed -n '1,20p' results/collaboration_bench.csv

python3 - <<'PY'
import csv
from pathlib import Path

path = Path("results/collaboration_bench.csv")
with path.open(newline="", encoding="utf-8") as handle:
    rows = list(csv.DictReader(handle))

text_rows = [row for row in rows if row["mode"] == "text_mode"]
structured_rows = [row for row in rows if row["mode"] == "structured_mode"]

text_total_tokens = sum(int(row["approx_tokens"]) for row in text_rows)
structured_total_tokens = sum(int(row["approx_tokens"]) for row in structured_rows)
text_total_latency = sum(float(row["total_latency_ms"]) for row in text_rows)
structured_total_latency = sum(float(row["total_latency_ms"]) for row in structured_rows)
structured_memory_hit_rate = (
    sum(1 for row in structured_rows if row["memory_hit"] == "true") / len(structured_rows)
    if structured_rows else 0.0
)
structured_total_saved_steps = sum(int(row["saved_steps"]) for row in structured_rows)

token_saving_ratio = (
    (text_total_tokens - structured_total_tokens) / text_total_tokens
    if text_total_tokens else 0.0
)
latency_saving_ratio = (
    (text_total_latency - structured_total_latency) / text_total_latency
    if text_total_latency else 0.0
)

print(f"text_mode total_tokens={text_total_tokens}")
print(f"structured_mode total_tokens={structured_total_tokens}")
print(f"token_saving_ratio={token_saving_ratio:.6f}")
print(f"text_mode total_latency_ms={text_total_latency:.3f}")
print(f"structured_mode total_latency_ms={structured_total_latency:.3f}")
print(f"latency_saving_ratio={latency_saving_ratio:.6f}")
print(f"structured_mode memory_hit_rate={structured_memory_hit_rate:.4f}")
print(f"structured_mode total_saved_steps={structured_total_saved_steps}")
PY
