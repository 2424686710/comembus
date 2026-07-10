#!/usr/bin/env bash
set -euo pipefail

mkdir -p results

python3 benchmarks/bench_embedding_codec.py \
  --dimensions 32,64,128,384,768 \
  --rounds "${COMEMBUS_EMBEDDING_ROUNDS:-30}" \
  --warmup "${COMEMBUS_EMBEDDING_WARMUP:-3}" \
  --output results/embedding_codec.csv

python3 - <<'PY'
import csv
rows = list(csv.DictReader(open("results/embedding_codec.csv", newline="", encoding="utf-8")))
if not rows or not all(row["checksum_ok"] == "true" for row in rows):
    raise SystemExit("embedding checksum verification failed")
vector_rows = [row for row in rows if row["mode"] != "summary_text"]
if not all(row["cosine_similarity_preserved"] == "true" for row in vector_rows):
    raise SystemExit("embedding cosine similarity was not preserved")
print("embedding codec acceptance checks passed")
PY

if find /dev/shm -maxdepth 1 -name 'comembus_*' -print -quit 2>/dev/null | grep -q .; then
  echo "shared-memory residue detected" >&2
  exit 1
fi
