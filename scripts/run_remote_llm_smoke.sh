#!/usr/bin/env bash
set -euo pipefail

if [ -z "${COMEMBUS_LLM_ENDPOINT:-}" ] || [ -z "${COMEMBUS_LLM_MODEL:-}" ] || [ -z "${COMEMBUS_LLM_API_KEY:-}" ]; then
  echo "SKIP: remote LLM env not configured"
  exit 0
fi

agent_output="$(
  python3 examples/incident_diagnosis_mock/run_llm_agent_demo.py \
    --provider openai_compatible \
    --endpoint "$COMEMBUS_LLM_ENDPOINT" \
    --model "$COMEMBUS_LLM_MODEL"
)"
printf '%s\n' "$agent_output"

multiagent_output="$(
  python3 examples/incident_diagnosis_mock/run_llm_multiagent_smoke.py \
    --provider openai_compatible \
    --endpoint "$COMEMBUS_LLM_ENDPOINT" \
    --model "$COMEMBUS_LLM_MODEL" \
    --llm-agents planner,review
)"
printf '%s\n' "$multiagent_output"

if printf '%s\n%s\n' "$agent_output" "$multiagent_output" | grep -q 'used_fallback=true\|used_fallback_count=[1-9]'; then
  echo "WARNING: remote LLM call fell back to mock"
else
  echo "remote_llm_used_fallback=false"
fi
