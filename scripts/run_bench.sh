#!/usr/bin/env bash
set -euo pipefail

python3 benchmarks/bench_transport.py \
  --modes direct_uds,shm_ref,adaptive \
  --sizes 1KB,16KB,64KB,1MB,8MB \
  --receivers 1,2,4 \
  --rounds 10 \
  --output results/transport_bench.csv
