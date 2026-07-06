#!/usr/bin/env bash
set -euo pipefail

if [[ -f /etc/openEuler-release ]]; then
  echo "== /etc/openEuler-release =="
  cat /etc/openEuler-release
elif [[ -f /etc/os-release ]]; then
  echo "== /etc/os-release =="
  cat /etc/os-release
else
  echo "missing OS release metadata" >&2
  exit 1
fi

echo "== python3 version =="
python3 --version

echo "== stdlib module import check =="
python3 - <<'PY'
import multiprocessing.shared_memory
import socket
import sqlite3

print("socket:", socket.__name__)
print("sqlite3:", sqlite3.sqlite_version)
print("shared_memory:", multiprocessing.shared_memory.__name__)
PY

if [[ ! -d /dev/shm ]]; then
  echo "/dev/shm is not available" >&2
  exit 1
fi

echo "== /dev/shm capacity =="
df -h /dev/shm

