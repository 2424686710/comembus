#!/usr/bin/env python3
"""Rigorous transport benchmark with fixed and calibrated adaptive policies."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import random
import sys
from typing import Dict, Iterable, List, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comembus.metrics.statistics import summarize
from comembus.transport.adaptive import AdaptiveTransportPolicy, DIRECT_UDS, SHM_REF
from comembus.transport.calibrator import (
    AdaptiveTransportCalibrator,
    DEFAULT_RANDOM_SEED,
    DEFAULT_RECEIVERS,
    DEFAULT_ROUNDS as CALIBRATION_ROUNDS,
    DEFAULT_SIZES,
    DEFAULT_WARMUP as CALIBRATION_WARMUP,
    deterministic_payload,
    measure_transport_once,
)


BENCHMARK_MODES = (
    DIRECT_UDS,
    SHM_REF,
    "fixed_adaptive",
    "calibrated_adaptive",
)

CSV_FIELDS = [
    "mode",
    "selected_mode",
    "size_bytes",
    "receivers",
    "round",
    "random_seed",
    "checksum_ok",
    "latency_ms",
    "mean_latency_ms",
    "p50_latency_ms",
    "p95_latency_ms",
    "p99_latency_ms",
    "latency_stddev_ms",
    "latency_ci95_lower_ms",
    "latency_ci95_upper_ms",
    "min_latency_ms",
    "max_latency_ms",
    "cpu_time_ms",
    "peak_rss_kb",
    "voluntary_context_switches",
    "involuntary_context_switches",
    "sent_bytes",
    "received_bytes",
    "wire_bytes",
    "shm_bytes_written",
    "shm_bytes_read",
    "message_count",
    "throughput_mib_s",
]


def benchmark_rows(
    sizes: Sequence[int] = DEFAULT_SIZES,
    receivers: Sequence[int] = DEFAULT_RECEIVERS,
    rounds: int = 30,
    warmup: int = 3,
    modes: Sequence[str] = BENCHMARK_MODES,
    profile_path: str | Path = "results/transport_profile.json",
    random_seed: int = DEFAULT_RANDOM_SEED,
) -> List[Dict[str, object]]:
    if rounds <= 0:
        raise ValueError("rounds must be positive")
    if warmup < 0:
        raise ValueError("warmup must be non-negative")
    if not sizes or any(int(size) <= 0 for size in sizes):
        raise ValueError("sizes must contain positive values")
    if not receivers or any(int(count) <= 0 for count in receivers):
        raise ValueError("receivers must contain positive values")
    for mode in modes:
        if mode not in BENCHMARK_MODES:
            raise ValueError(f"unsupported rigorous transport mode: {mode}")

    random.seed(random_seed)
    fixed_policy = AdaptiveTransportPolicy()
    calibrated_policy = AdaptiveTransportPolicy.from_profile(profile_path)
    rows: List[Dict[str, object]] = []
    for mode in modes:
        for receiver_count in receivers:
            for size_bytes in sizes:
                selected_mode = _selected_mode(
                    mode,
                    int(size_bytes),
                    int(receiver_count),
                    fixed_policy,
                    calibrated_policy,
                )
                for warmup_index in range(warmup):
                    data = deterministic_payload(
                        int(size_bytes), random_seed, -(warmup_index + 1)
                    )
                    result = measure_transport_once(
                        selected_mode, data, int(receiver_count), -(warmup_index + 1)
                    )
                    if not result.checksum_ok:
                        raise RuntimeError("transport benchmark warmup checksum failed")

                group: List[Dict[str, object]] = []
                for round_index in range(1, rounds + 1):
                    data = deterministic_payload(int(size_bytes), random_seed, round_index)
                    measurement = measure_transport_once(
                        selected_mode, data, int(receiver_count), round_index
                    )
                    if not measurement.checksum_ok:
                        raise RuntimeError("transport benchmark checksum failed")
                    row = measurement.to_dict()
                    row["mode"] = mode
                    row["random_seed"] = random_seed
                    group.append(row)

                latency = summarize([float(row["latency_ms"]) for row in group])
                for row in group:
                    row.update(
                        {
                            "mean_latency_ms": latency["mean"],
                            "p50_latency_ms": latency["p50"],
                            "p95_latency_ms": latency["p95"],
                            "p99_latency_ms": latency["p99"],
                            "latency_stddev_ms": latency["standard_deviation"],
                            "latency_ci95_lower_ms": latency["ci95_lower"],
                            "latency_ci95_upper_ms": latency["ci95_upper"],
                            "min_latency_ms": latency["min"],
                            "max_latency_ms": latency["max"],
                        }
                    )
                rows.extend(group)
    return rows


def write_results(path: str | Path, rows: Iterable[Mapping[str, object]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for raw_row in rows:
            row = dict(raw_row)
            row["checksum_ok"] = str(bool(row["checksum_ok"])).lower()
            for field in (
                "latency_ms",
                "mean_latency_ms",
                "p50_latency_ms",
                "p95_latency_ms",
                "p99_latency_ms",
                "latency_stddev_ms",
                "latency_ci95_lower_ms",
                "latency_ci95_upper_ms",
                "min_latency_ms",
                "max_latency_ms",
                "cpu_time_ms",
                "throughput_mib_s",
            ):
                row[field] = f"{float(row[field]):.6f}"
            writer.writerow(row)


def _selected_mode(
    mode: str,
    size_bytes: int,
    receivers: int,
    fixed_policy: AdaptiveTransportPolicy,
    calibrated_policy: AdaptiveTransportPolicy,
) -> str:
    if mode in (DIRECT_UDS, SHM_REF):
        return mode
    if mode == "fixed_adaptive":
        return fixed_policy.choose_mode(size_bytes, receivers)
    return calibrated_policy.choose_mode(size_bytes, receivers)


def parse_size(token: str) -> int:
    normalized = token.strip().upper()
    for suffix, multiplier in (
        ("MB", 1024 * 1024),
        ("KB", 1024),
        ("B", 1),
    ):
        if normalized.endswith(suffix):
            raw_number = normalized[: -len(suffix)]
            break
    else:
        raise ValueError(f"unsupported size: {token}")
    value = int(raw_number)
    if value <= 0:
        raise ValueError("sizes must be positive")
    return value * multiplier


def parse_sizes(spec: str) -> List[int]:
    return [parse_size(token) for token in spec.split(",") if token.strip()]


def parse_ints(spec: str) -> List[int]:
    values = [int(token.strip()) for token in spec.split(",") if token.strip()]
    if not values or any(value <= 0 for value in values):
        raise ValueError("expected positive comma-separated integers")
    return values


def parse_modes(spec: str) -> List[str]:
    modes = [token.strip() for token in spec.split(",") if token.strip()]
    if not modes:
        raise ValueError("at least one mode is required")
    return modes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes", default="1KB,4KB,16KB,64KB,256KB,1MB,8MB"
    )
    parser.add_argument("--receivers", default="1,2,4,8")
    parser.add_argument("--rounds", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--calibration-rounds", type=int, default=CALIBRATION_ROUNDS)
    parser.add_argument("--calibration-warmup", type=int, default=CALIBRATION_WARMUP)
    parser.add_argument("--random-seed", type=int, default=DEFAULT_RANDOM_SEED)
    parser.add_argument("--modes", default=",".join(BENCHMARK_MODES))
    parser.add_argument("--profile", default="results/transport_profile.json")
    parser.add_argument("--output", default="results/rigorous_transport.csv")
    parser.add_argument(
        "--skip-calibration",
        action="store_true",
        help="Load an existing profile, falling back to the fixed policy if absent",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        sizes = parse_sizes(args.sizes)
        receivers = parse_ints(args.receivers)
        modes = parse_modes(args.modes)
        if not args.skip_calibration:
            calibrator = AdaptiveTransportCalibrator(
                sizes=sizes,
                receivers=receivers,
                warmup=args.calibration_warmup,
                rounds=args.calibration_rounds,
                random_seed=args.random_seed,
            )
            profile = calibrator.calibrate(args.profile)
            thresholds = profile["thresholds_by_receivers"]
            print(f"calibrated receiver thresholds: {thresholds}")
        rows = benchmark_rows(
            sizes=sizes,
            receivers=receivers,
            rounds=args.rounds,
            warmup=args.warmup,
            modes=modes,
            profile_path=args.profile,
            random_seed=args.random_seed,
        )
        write_results(args.output, rows)
    except Exception as exc:
        print(f"rigorous transport benchmark failed: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {len(rows)} rigorous transport rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
