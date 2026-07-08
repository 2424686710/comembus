#!/usr/bin/env bash
set -euo pipefail

mkdir -p results

python3 examples/incident_diagnosis_mock/run_llm_agent_demo.py \
  --provider mock \
  --save-json results/llm_mock_smoke.json \
  --write-memory \
  --db-path results/llm_mock_memory.sqlite

python3 - <<'PY'
import json
from pathlib import Path

path = Path("results/llm_mock_smoke.json")
payload = json.loads(path.read_text(encoding="utf-8"))
print(f"mock_root_cause={payload['root_cause']}")
print(f"mock_report={payload['report']}")
print(f"mock_total_tokens={payload.get('total_tokens')}")
print(f"mock_used_fallback={str(bool(payload['used_fallback'])).lower()}")
PY

if [ -z "${COMEMBUS_LLM_ENDPOINT:-}" ] || [ -z "${COMEMBUS_LLM_MODEL:-}" ] || [ -z "${COMEMBUS_LLM_API_KEY:-}" ]; then
  echo "SKIP remote LLM and exit 0"
  exit 0
fi

python3 examples/incident_diagnosis_mock/run_llm_agent_demo.py \
  --provider openai_compatible \
  --endpoint "$COMEMBUS_LLM_ENDPOINT" \
  --model "$COMEMBUS_LLM_MODEL" \
  --save-json results/llm_remote_smoke.json \
  --write-memory \
  --db-path results/llm_remote_memory.sqlite

python3 - <<'PY'
import json
from pathlib import Path

mock_path = Path("results/llm_mock_smoke.json")
remote_path = Path("results/llm_remote_smoke.json")
mock_payload = json.loads(mock_path.read_text(encoding="utf-8"))
remote_payload = json.loads(remote_path.read_text(encoding="utf-8"))

print(f"remote_root_cause={remote_payload['root_cause']}")
print(f"remote_report={remote_payload['report']}")
print(f"remote_total_tokens={remote_payload.get('total_tokens')}")
print(f"remote_used_fallback={str(bool(remote_payload['used_fallback'])).lower()}")
print(f"report_different={str(mock_payload['report'] != remote_payload['report']).lower()}")
PY

if python3 - <<'PY'
import json
from pathlib import Path

payload = json.loads(Path("results/llm_remote_smoke.json").read_text(encoding="utf-8"))
raise SystemExit(0 if not payload.get("used_fallback") else 1)
PY
then
  :
else
  echo "WARNING: remote LLM call fell back to mock"
fi
