#!/usr/bin/env bash
set -euo pipefail

# Default offline path:
#   bash scripts/run_llm_demo.sh
#
# Optional local_http example:
#   python3 examples/incident_diagnosis_mock/run_llm_agent_demo.py \
#     --provider local_http \
#     --endpoint http://127.0.0.1:11434/v1/chat/completions \
#     --model your-local-model
python3 examples/incident_diagnosis_mock/run_llm_agent_demo.py --provider mock
