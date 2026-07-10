#!/usr/bin/env bash
set -euo pipefail

mkdir -p results

python3 benchmarks/bench_failure_recovery.py \
  --output results/failure_injection.csv

python3 - <<'PY'
import csv
from pathlib import Path

path = Path("results/failure_injection.csv")
with path.open(newline="", encoding="utf-8") as handle:
    rows = list(csv.DictReader(handle))

if len(rows) != 8:
    raise SystemExit(f"expected 8 failure scenarios, found {len(rows)}")
if not all(row["success"] == "true" for row in rows):
    raise SystemExit("one or more failure scenarios failed")
duplicate = next(row for row in rows if row["scenario"] == "duplicate_message_suppression")
restart = next(row for row in rows if row["scenario"] == "coordinator_crash_state_recovery")
object_row = next(row for row in rows if row["scenario"] == "object_lease_crash_reclaim")
if duplicate["duplicate_suppressed"] != "true":
    raise SystemExit("duplicate suppression was not observed")
if restart["state_recovered"] != "true":
    raise SystemExit("state recovery was not observed")
if object_row["object_reclaimed"] != "true":
    raise SystemExit("object reclamation was not observed")
if any(int(row["shm_residue_count"]) != 0 for row in rows):
    raise SystemExit("shared-memory residue was detected")
print("failure injection acceptance checks passed")
PY
