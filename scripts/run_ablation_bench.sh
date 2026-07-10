#!/usr/bin/env bash
set -euo pipefail

mkdir -p results/figures

python3 benchmarks/bench_ablation.py \
  --scenario-file examples/incident_diagnosis_mock/scenarios.jsonl \
  --rounds "${COMEMBUS_ABLATION_ROUNDS:-30}" \
  --warmup "${COMEMBUS_ABLATION_WARMUP:-3}" \
  --log-size-bytes "${COMEMBUS_ABLATION_LOG_BYTES:-262144}" \
  --random-seed "${COMEMBUS_BENCH_SEED:-20260710}" \
  --output results/ablation_bench.csv

python3 scripts/summarize_rigorous_results.py
