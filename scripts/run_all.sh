#!/usr/bin/env bash
set -euo pipefail

bash scripts/run_tests.sh
bash scripts/run_demo.sh
bash scripts/run_bench.sh
bash scripts/run_agent_demo.sh
bash scripts/run_state_bench.sh
python3 examples/incident_diagnosis_mock/run_memory_reuse_demo.py
bash scripts/run_memory_bench.sh
python3 examples/incident_diagnosis_mock/run_collaboration_modes_demo.py
bash scripts/run_collaboration_bench.sh
python3 scripts/summarize_all_results.py

sed -n '1,120p' results/summary_report.md

if [ -d /dev/shm ]; then
  leftover="$(find /dev/shm -maxdepth 1 -name 'comembus_*' -print)"
  if [ -n "${leftover}" ]; then
    echo "warning: leftover shared memory objects detected"
    echo "${leftover}"
  fi
fi
