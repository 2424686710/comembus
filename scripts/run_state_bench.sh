#!/usr/bin/env bash
set -euo pipefail

python3 benchmarks/bench_state_patch.py \
  --output results/state_patch_bench.csv

sed -n '1,20p' results/state_patch_bench.csv
