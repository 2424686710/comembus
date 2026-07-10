#!/usr/bin/env bash
set -euo pipefail

mkdir -p results/figures

python3 benchmarks/bench_rigorous_transport.py \
  --sizes 1KB,4KB,16KB,64KB,256KB,1MB,8MB \
  --receivers 1,2,4,8 \
  --rounds "${COMEMBUS_RIGOROUS_ROUNDS:-30}" \
  --warmup "${COMEMBUS_RIGOROUS_WARMUP:-3}" \
  --calibration-rounds "${COMEMBUS_CALIBRATION_ROUNDS:-20}" \
  --calibration-warmup "${COMEMBUS_CALIBRATION_WARMUP:-3}" \
  --random-seed "${COMEMBUS_BENCH_SEED:-20260710}" \
  --profile results/transport_profile.json \
  --output results/rigorous_transport.csv

python3 scripts/summarize_rigorous_results.py
