#!/usr/bin/env bash
set -euo pipefail

python3 -m py_compile $(find comembus examples tests -name "*.py")
python3 -m unittest discover -s tests -v

