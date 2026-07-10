#!/usr/bin/env bash
set -euo pipefail

python3 examples/incident_diagnosis_mock/run_reliable_agent_demo.py

leftover="$(find /dev/shm -maxdepth 1 -name 'comembus_*' -print)"
if [[ -n "${leftover}" ]]; then
  echo "shared-memory residue detected after reliable demo" >&2
  echo "${leftover}" >&2
  exit 1
fi
