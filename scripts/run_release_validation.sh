#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

# The release audit is fully offline and must never forward credentials.
unset COMEMBUS_LLM_API_KEY OPENAI_API_KEY || true

run_step() {
  local name="$1"
  shift
  echo "== release validation: ${name} =="
  "$@"
}

run_step check_env bash scripts/check_env.sh
run_step run_tests bash scripts/run_tests.sh
run_step run_all bash scripts/run_all.sh
run_step run_reliable_agent_demo bash scripts/run_reliable_agent_demo.sh
run_step run_ablation_bench bash scripts/run_ablation_bench.sh
run_step run_rigorous_bench bash scripts/run_rigorous_bench.sh
run_step run_failure_bench bash scripts/run_failure_bench.sh
run_step run_embedding_bench bash scripts/run_embedding_bench.sh
run_step run_memory_quality_bench bash scripts/run_memory_quality_bench.sh
run_step run_llm_demo bash scripts/run_llm_demo.sh
run_step run_codeact_demo bash scripts/run_codeact_demo.sh

echo "== release validation: /dev/shm residue check =="
leftover="$(find /dev/shm -maxdepth 1 -name 'comembus_*' -print)"
if [[ -n "${leftover}" ]]; then
  echo "shared-memory residue detected" >&2
  echo "${leftover}" >&2
  exit 1
fi
echo "shm_residue_count=0"

run_step create_release_manifest python3 scripts/create_release_manifest.py
echo "OK: CoMemBus release validation completed"
