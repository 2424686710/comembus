#!/usr/bin/env python3
"""Summarize rigorous CoMemBus CSV outputs and generate dependency-free SVGs."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from html import escape
import json
import math
from pathlib import Path
import statistics as stdlib_statistics
import sys
from typing import Dict, Iterable, List, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comembus.metrics.statistics import percentile, summarize


def read_csv_if_present(path: str | Path) -> List[Dict[str, str]]:
    input_path = Path(path)
    if not input_path.exists():
        return []
    with input_path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def aggregate_ablation(rows: Sequence[Mapping[str, str]]) -> Dict[str, Dict[str, object]]:
    grouped = _group_by(rows, "mode")
    result: Dict[str, Dict[str, object]] = {}
    for mode, mode_rows in grouped.items():
        latencies = [_float(row, "latency_ms") for row in mode_rows]
        latency = summarize(latencies)
        result[mode] = {
            "rows": len(mode_rows),
            "root_cause_correct_rate": _true_rate(mode_rows, "root_cause_correct"),
            "latency_ms": latency,
            "mean_cpu_time_ms": _mean_field(mode_rows, "cpu_time_ms"),
            "peak_rss_kb": max(_int(row, "peak_rss_kb") for row in mode_rows),
            "mean_wire_bytes": _mean_field(mode_rows, "wire_bytes"),
            "mean_shm_bytes_written": _mean_field(mode_rows, "shm_bytes_written"),
            "mean_estimated_tokens": _mean_field(mode_rows, "estimated_tokens"),
            "token_metric_type": (
                mode_rows[0].get("token_metric_type", "unknown") if mode_rows else "unknown"
            ),
            "mean_state_bytes": _mean_field(mode_rows, "state_bytes"),
            "memory_hit_rate": _true_rate(mode_rows, "memory_hit"),
            "total_saved_steps": sum(_int(row, "saved_steps") for row in mode_rows),
            "mean_message_count": _mean_field(mode_rows, "message_count"),
        }
    return result


def aggregate_transport(rows: Sequence[Mapping[str, str]]) -> Dict[str, Dict[str, object]]:
    grouped = _group_by(rows, "mode")
    result: Dict[str, Dict[str, object]] = {}
    for mode, mode_rows in grouped.items():
        latency = summarize([_float(row, "latency_ms") for row in mode_rows])
        result[mode] = {
            "rows": len(mode_rows),
            "checksum_ok_rate": _true_rate(mode_rows, "checksum_ok"),
            "latency_ms": latency,
            "mean_cpu_time_ms": _mean_field(mode_rows, "cpu_time_ms"),
            "peak_rss_kb": max(_int(row, "peak_rss_kb") for row in mode_rows),
            "mean_wire_bytes": _mean_field(mode_rows, "wire_bytes"),
            "mean_shm_bytes_written": _mean_field(mode_rows, "shm_bytes_written"),
            "mean_throughput_mib_s": _mean_field(mode_rows, "throughput_mib_s"),
        }
    return result


def acceptance_checks(
    ablation: Mapping[str, Mapping[str, object]],
    transport: Mapping[str, Mapping[str, object]],
) -> Dict[str, bool | None]:
    checks: Dict[str, bool | None] = {
        "all_root_causes_correct": None,
        "structured_full_wire_lower_than_text_full_context": None,
        "no_shm_wire_bytes_increase": None,
        "no_patch_state_bytes_increase": None,
        "no_memory_saved_steps_zero": None,
        "calibrated_within_five_percent_of_fixed": None,
        "all_transport_checksums_ok": None,
    }
    if ablation:
        checks["all_root_causes_correct"] = all(
            float(values["root_cause_correct_rate"]) == 1.0
            for values in ablation.values()
        )
        full = ablation.get("structured_full")
        text = ablation.get("text_full_context")
        no_shm = ablation.get("structured_no_shm")
        no_patch = ablation.get("structured_no_patch")
        no_memory = ablation.get("structured_no_memory")
        if full and text:
            checks["structured_full_wire_lower_than_text_full_context"] = (
                float(full["mean_wire_bytes"]) < float(text["mean_wire_bytes"])
            )
        if full and no_shm:
            checks["no_shm_wire_bytes_increase"] = float(
                no_shm["mean_wire_bytes"]
            ) > float(full["mean_wire_bytes"])
        if full and no_patch:
            checks["no_patch_state_bytes_increase"] = float(
                no_patch["mean_state_bytes"]
            ) > float(full["mean_state_bytes"])
        if no_memory:
            checks["no_memory_saved_steps_zero"] = int(
                no_memory["total_saved_steps"]
            ) == 0
    if transport:
        checks["all_transport_checksums_ok"] = all(
            float(values["checksum_ok_rate"]) == 1.0
            for values in transport.values()
        )
        fixed = transport.get("fixed_adaptive")
        calibrated = transport.get("calibrated_adaptive")
        if fixed and calibrated:
            fixed_mean = float(fixed["latency_ms"]["mean"])
            calibrated_mean = float(calibrated["latency_ms"]["mean"])
            checks["calibrated_within_five_percent_of_fixed"] = (
                calibrated_mean <= fixed_mean * 1.05
            )
    return checks


def write_summary_markdown(
    path: str | Path,
    ablation: Mapping[str, Mapping[str, object]],
    transport: Mapping[str, Mapping[str, object]],
    checks: Mapping[str, bool | None],
    ablation_rows: int,
    transport_rows: int,
) -> None:
    lines = [
        "# CoMemBus v1.3 rigorous benchmark summary",
        "",
        f"- Ablation rows: {ablation_rows}",
        f"- Rigorous transport rows: {transport_rows}",
        "- Token metric: deterministic character estimate at 4 characters/token; it is not a model-reported token count.",
        "- Wire bytes: exact bytes recorded by `send_frame`, including each 4-byte frame header.",
        "- Shared-memory bytes are reported separately from UDS wire bytes.",
        "",
    ]
    if ablation:
        lines.extend(
            [
                "## Ablation modes",
                "",
                "| mode | rows | correct | mean ms | p50 | p95 | p99 | mean wire bytes | mean state bytes | saved steps |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for mode, values in ablation.items():
            latency = values["latency_ms"]
            lines.append(
                "| {mode} | {rows} | {correct:.3f} | {mean:.3f} | {p50:.3f} | "
                "{p95:.3f} | {p99:.3f} | {wire:.1f} | {state:.1f} | {saved} |".format(
                    mode=mode,
                    rows=values["rows"],
                    correct=float(values["root_cause_correct_rate"]),
                    mean=float(latency["mean"]),
                    p50=float(latency["p50"]),
                    p95=float(latency["p95"]),
                    p99=float(latency["p99"]),
                    wire=float(values["mean_wire_bytes"]),
                    state=float(values["mean_state_bytes"]),
                    saved=values["total_saved_steps"],
                )
            )
        lines.append("")
    if transport:
        lines.extend(
            [
                "## Transport modes",
                "",
                "| mode | rows | checksum | mean ms | p50 | p95 | p99 | throughput MiB/s | mean wire bytes |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for mode, values in transport.items():
            latency = values["latency_ms"]
            lines.append(
                "| {mode} | {rows} | {checksum:.3f} | {mean:.3f} | {p50:.3f} | "
                "{p95:.3f} | {p99:.3f} | {throughput:.3f} | {wire:.1f} |".format(
                    mode=mode,
                    rows=values["rows"],
                    checksum=float(values["checksum_ok_rate"]),
                    mean=float(latency["mean"]),
                    p50=float(latency["p50"]),
                    p95=float(latency["p95"]),
                    p99=float(latency["p99"]),
                    throughput=float(values["mean_throughput_mib_s"]),
                    wire=float(values["mean_wire_bytes"]),
                )
            )
        lines.append("")
    lines.extend(["## Acceptance checks", ""])
    for name, passed in checks.items():
        status = "not evaluated" if passed is None else ("PASS" if passed else "FAIL")
        lines.append(f"- `{name}`: {status}")
    lines.append("")
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def generate_figures(
    figures_dir: str | Path,
    ablation_rows: Sequence[Mapping[str, str]],
    transport_rows: Sequence[Mapping[str, str]],
) -> None:
    output_dir = Path(figures_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ablation = aggregate_ablation(ablation_rows) if ablation_rows else {}
    transport = aggregate_transport(transport_rows) if transport_rows else {}
    _write_bar_svg(
        output_dir / "ablation_latency.svg",
        "Ablation mean latency",
        [(mode, float(values["latency_ms"]["mean"])) for mode, values in ablation.items()],
        "milliseconds",
    )
    _write_bar_svg(
        output_dir / "ablation_tokens.svg",
        "Ablation estimated tokens (not model tokens)",
        [(mode, float(values["mean_estimated_tokens"])) for mode, values in ablation.items()],
        "estimated tokens",
    )
    percentile_series = []
    for mode, values in transport.items():
        latency = values["latency_ms"]
        percentile_series.append(
            (mode, [float(latency["p50"]), float(latency["p95"]), float(latency["p99"])])
        )
    _write_grouped_bar_svg(
        output_dir / "latency_percentiles.svg",
        "Transport latency percentiles",
        ["p50", "p95", "p99"],
        percentile_series,
        "milliseconds",
    )
    _write_transport_crossover_svg(
        output_dir / "transport_crossover.svg", transport_rows
    )


def _write_bar_svg(
    path: Path,
    title: str,
    values: Sequence[tuple[str, float]],
    unit: str,
) -> None:
    width, height = 1200, 520
    left, top, right, bottom = 85, 55, 25, 155
    plot_w, plot_h = width - left - right, height - top - bottom
    maximum = max((value for _, value in values), default=1.0) or 1.0
    parts = _svg_header(width, height, title)
    parts.append(_axes(left, top, plot_w, plot_h, unit))
    count = max(1, len(values))
    step = plot_w / count
    bar_w = step * 0.64
    for index, (label, value) in enumerate(values):
        bar_h = (value / maximum) * (plot_h - 15)
        x = left + (index * step) + ((step - bar_w) / 2)
        y = top + plot_h - bar_h
        parts.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_w:.2f}" height="{bar_h:.2f}" fill="#3b82f6"/>'
        )
        parts.append(
            f'<text x="{x + bar_w / 2:.2f}" y="{y - 5:.2f}" text-anchor="middle" font-size="11">{value:.2f}</text>'
        )
        parts.append(
            f'<text transform="translate({x + bar_w / 2:.2f},{top + plot_h + 14:.2f}) rotate(45)" font-size="11">{escape(label)}</text>'
        )
    parts.append("</g></svg>\n")
    path.write_text("\n".join(parts), encoding="utf-8")


def _write_grouped_bar_svg(
    path: Path,
    title: str,
    labels: Sequence[str],
    series: Sequence[tuple[str, Sequence[float]]],
    unit: str,
) -> None:
    width, height = 1000, 520
    left, top, right, bottom = 85, 55, 220, 80
    plot_w, plot_h = width - left - right, height - top - bottom
    maximum = max((value for _, values in series for value in values), default=1.0) or 1.0
    colors = ["#2563eb", "#16a34a", "#ea580c", "#9333ea"]
    parts = _svg_header(width, height, title)
    parts.append(_axes(left, top, plot_w, plot_h, unit))
    group_w = plot_w / max(1, len(labels))
    bar_w = group_w * 0.7 / max(1, len(series))
    for group_index, label in enumerate(labels):
        for series_index, (series_name, values) in enumerate(series):
            value = values[group_index]
            bar_h = (value / maximum) * (plot_h - 15)
            x = left + group_index * group_w + group_w * 0.15 + series_index * bar_w
            y = top + plot_h - bar_h
            parts.append(
                f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_w - 2:.2f}" height="{bar_h:.2f}" fill="{colors[series_index % len(colors)]}"/>'
            )
        parts.append(
            f'<text x="{left + (group_index + 0.5) * group_w:.2f}" y="{top + plot_h + 24}" text-anchor="middle">{escape(label)}</text>'
        )
    for index, (name, _) in enumerate(series):
        y = top + 18 + index * 24
        parts.append(
            f'<rect x="{width - right + 25}" y="{y - 11}" width="14" height="14" fill="{colors[index % len(colors)]}"/>'
        )
        parts.append(
            f'<text x="{width - right + 46}" y="{y}" font-size="12">{escape(name)}</text>'
        )
    parts.append("</g></svg>\n")
    path.write_text("\n".join(parts), encoding="utf-8")


def _write_transport_crossover_svg(
    path: Path, rows: Sequence[Mapping[str, str]]
) -> None:
    filtered = [row for row in rows if row.get("mode") in {"direct_uds", "shm_ref"}]
    grouped: Dict[tuple[str, int, int], List[float]] = {}
    for row in filtered:
        key = (row["mode"], _int(row, "receivers"), _int(row, "size_bytes"))
        grouped.setdefault(key, []).append(_float(row, "latency_ms"))
    sizes = sorted({key[2] for key in grouped})
    receiver_counts = sorted({key[1] for key in grouped})
    width, height = 1100, 560
    left, top, right, bottom = 90, 55, 260, 85
    plot_w, plot_h = width - left - right, height - top - bottom
    series: List[tuple[str, List[float]]] = []
    for receiver_count in receiver_counts:
        for mode in ("direct_uds", "shm_ref"):
            values = [
                stdlib_statistics.fmean(grouped.get((mode, receiver_count, size), [0.0]))
                for size in sizes
            ]
            series.append((f"{mode} r={receiver_count}", values))
    maximum = max((value for _, values in series for value in values), default=1.0) or 1.0
    colors = ["#2563eb", "#16a34a", "#ea580c", "#9333ea", "#0891b2", "#dc2626", "#4f46e5", "#65a30d"]
    parts = _svg_header(width, height, "Measured transport crossover")
    parts.append(_axes(left, top, plot_w, plot_h, "milliseconds"))
    for index, (name, values) in enumerate(series):
        points = []
        for value_index, value in enumerate(values):
            x = left + (value_index / max(1, len(sizes) - 1)) * plot_w
            y = top + plot_h - ((value / maximum) * (plot_h - 10))
            points.append(f"{x:.2f},{y:.2f}")
            parts.append(
                f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3" fill="{colors[index % len(colors)]}"/>'
            )
        parts.append(
            f'<polyline points="{" ".join(points)}" fill="none" stroke="{colors[index % len(colors)]}" stroke-width="2"/>'
        )
        legend_y = top + 18 + index * 23
        parts.append(
            f'<line x1="{width - right + 20}" y1="{legend_y - 4}" x2="{width - right + 43}" y2="{legend_y - 4}" stroke="{colors[index % len(colors)]}" stroke-width="3"/>'
        )
        parts.append(
            f'<text x="{width - right + 50}" y="{legend_y}" font-size="11">{escape(name)}</text>'
        )
    for index, size in enumerate(sizes):
        x = left + (index / max(1, len(sizes) - 1)) * plot_w
        parts.append(
            f'<text x="{x:.2f}" y="{top + plot_h + 24}" text-anchor="middle" font-size="10">{escape(_format_bytes(size))}</text>'
        )
    parts.append("</g></svg>\n")
    path.write_text("\n".join(parts), encoding="utf-8")


def _svg_header(width: int, height: int, title: str) -> List[str]:
    return [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{width / 2}" y="30" text-anchor="middle" font-size="20" font-family="sans-serif">{escape(title)}</text>',
        '<g font-family="sans-serif" fill="#111827">',
    ]


def _axes(left: int, top: int, width: int, height: int, unit: str) -> str:
    return (
        f'<path d="M {left} {top} V {top + height} H {left + width}" '
        'fill="none" stroke="#374151" stroke-width="1"/>'
        f'<text x="18" y="{top + height / 2}" transform="rotate(-90 18 {top + height / 2})" '
        f'text-anchor="middle" font-size="12">{escape(unit)}</text>'
    )


def _group_by(
    rows: Sequence[Mapping[str, str]], key: str
) -> Dict[str, List[Mapping[str, str]]]:
    grouped: Dict[str, List[Mapping[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row[key], []).append(row)
    return grouped


def _float(row: Mapping[str, str], field: str) -> float:
    return float(row.get(field, "0") or 0.0)


def _int(row: Mapping[str, str], field: str) -> int:
    return int(float(row.get(field, "0") or 0))


def _mean_field(rows: Sequence[Mapping[str, str]], field: str) -> float:
    return stdlib_statistics.fmean(_float(row, field) for row in rows)


def _true_rate(rows: Sequence[Mapping[str, str]], field: str) -> float:
    return sum(row.get(field, "").lower() == "true" for row in rows) / len(rows)


def _format_bytes(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size // (1024 * 1024)}MB"
    if size >= 1024:
        return f"{size // 1024}KB"
    return f"{size}B"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ablation", default="results/ablation_bench.csv")
    parser.add_argument("--transport", default="results/rigorous_transport.csv")
    parser.add_argument("--summary", default="results/rigorous_summary.md")
    parser.add_argument("--metrics", default="results/rigorous_metrics.json")
    parser.add_argument("--figures-dir", default="results/figures")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ablation_rows = read_csv_if_present(args.ablation)
    transport_rows = read_csv_if_present(args.transport)
    if not ablation_rows and not transport_rows:
        print("no rigorous CSV results found", file=sys.stderr)
        return 1
    ablation = aggregate_ablation(ablation_rows) if ablation_rows else {}
    transport = aggregate_transport(transport_rows) if transport_rows else {}
    checks = acceptance_checks(ablation, transport)
    metrics = {
        "suite_version": "1.3",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ablation_row_count": len(ablation_rows),
        "transport_row_count": len(transport_rows),
        "ablation": ablation,
        "transport": transport,
        "acceptance_checks": checks,
    }
    metrics_path = Path(args.metrics)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_summary_markdown(
        args.summary,
        ablation,
        transport,
        checks,
        len(ablation_rows),
        len(transport_rows),
    )
    generate_figures(args.figures_dir, ablation_rows, transport_rows)
    print(
        f"summarized {len(ablation_rows)} ablation rows and "
        f"{len(transport_rows)} transport rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
