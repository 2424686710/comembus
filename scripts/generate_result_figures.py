#!/usr/bin/env python3
"""Generate SVG result figures from CoMemBus benchmark outputs."""

from __future__ import annotations

import csv
import json
import math
import os
import sys
from typing import Dict, Iterable, List, Mapping, Sequence


FIGURE_FILENAMES = [
    "transport_latency.svg",
    "state_patch_bytes.svg",
    "collaboration_tokens.svg",
    "collaboration_latency.svg",
    "memory_reuse.svg",
    "capability_embedding_counts.svg",
]


def generate_result_figures(
    results_dir: str = "results",
    output_dir: str | None = None,
) -> Dict[str, object]:
    base_dir = os.path.abspath(results_dir)
    figure_dir = os.path.abspath(output_dir or os.path.join(base_dir, "figures"))
    os.makedirs(figure_dir, exist_ok=True)

    warnings: List[str] = []
    transport_rows = _load_csv(os.path.join(base_dir, "transport_bench.csv"), warnings)
    state_rows = _load_csv(os.path.join(base_dir, "state_patch_bench.csv"), warnings)
    memory_rows = _load_csv(os.path.join(base_dir, "memory_reuse_bench.csv"), warnings)
    collaboration_rows = _load_csv(os.path.join(base_dir, "collaboration_bench.csv"), warnings)
    summary_metrics = _load_json(os.path.join(base_dir, "summary_metrics.json"), warnings)

    generated: List[str] = []
    generated.append(
        _write_svg(
            os.path.join(figure_dir, "transport_latency.svg"),
            _transport_chart_svg(transport_rows, summary_metrics),
        )
    )
    generated.append(
        _write_svg(
            os.path.join(figure_dir, "state_patch_bytes.svg"),
            _state_patch_chart_svg(state_rows, summary_metrics),
        )
    )
    generated.append(
        _write_svg(
            os.path.join(figure_dir, "collaboration_tokens.svg"),
            _collaboration_tokens_svg(collaboration_rows, summary_metrics),
        )
    )
    generated.append(
        _write_svg(
            os.path.join(figure_dir, "collaboration_latency.svg"),
            _collaboration_latency_svg(collaboration_rows, summary_metrics),
        )
    )
    generated.append(
        _write_svg(
            os.path.join(figure_dir, "memory_reuse.svg"),
            _memory_reuse_svg(memory_rows, summary_metrics),
        )
    )
    generated.append(
        _write_svg(
            os.path.join(figure_dir, "capability_embedding_counts.svg"),
            _capability_embedding_svg(collaboration_rows, summary_metrics),
        )
    )

    return {
        "warnings": warnings,
        "generated_files": generated,
    }


def _load_csv(path: str, warnings: List[str]) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        warnings.append(f"missing csv: {path}")
        return []
    with open(path, "r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _load_json(path: str, warnings: List[str]) -> Dict[str, object]:
    if not os.path.exists(path):
        warnings.append(f"missing json: {path}")
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        warnings.append(f"invalid json object: {path}")
        return {}
    return payload


def _transport_chart_svg(
    rows: Sequence[Mapping[str, str]],
    summary_metrics: Mapping[str, object],
) -> str:
    values: List[tuple[str, float]] = []
    transport = summary_metrics.get("transport", {})
    if isinstance(transport, dict):
        by_mode = transport.get("by_mode", {})
        if isinstance(by_mode, dict):
            for mode in ("direct_uds", "shm_ref", "adaptive"):
                metrics = by_mode.get(mode)
                if isinstance(metrics, dict):
                    values.append((mode, float(metrics.get("avg_latency_ms", 0.0))))
    if not values:
        grouped: Dict[str, List[float]] = {}
        for row in rows:
            grouped.setdefault(row.get("mode", ""), []).append(_as_float(row.get("latency_ms", "0")))
        for mode in ("direct_uds", "shm_ref", "adaptive"):
            mode_rows = grouped.get(mode, [])
            if mode_rows:
                values.append((mode, sum(mode_rows) / len(mode_rows)))
    return _bar_chart_svg(
        title="Transport Average Latency (ms)",
        items=values,
        y_label="latency_ms",
        empty_message="No transport benchmark data found.",
    )


def _state_patch_chart_svg(
    rows: Sequence[Mapping[str, str]],
    summary_metrics: Mapping[str, object],
) -> str:
    grouped: List[tuple[str, float, float]] = []
    state_patch = summary_metrics.get("state_patch", {})
    if isinstance(state_patch, dict):
        by_state_size = state_patch.get("by_state_size", {})
        if isinstance(by_state_size, dict):
            for name in ("small", "medium", "large"):
                metrics = by_state_size.get(name)
                if isinstance(metrics, dict):
                    grouped.append(
                        (
                            name,
                            float(metrics.get("full_state_bytes", 0)),
                            float(metrics.get("patch_bytes", 0)),
                        )
                    )
    if not grouped:
        rows_by_size: Dict[str, Dict[str, float]] = {}
        for row in rows:
            if row.get("mode") != "patch":
                continue
            rows_by_size[row.get("state_size", "")] = {
                "full": _as_float(row.get("full_state_bytes", "0")),
                "patch": _as_float(row.get("patch_bytes", "0")),
            }
        for name in ("small", "medium", "large"):
            if name in rows_by_size:
                grouped.append((name, rows_by_size[name]["full"], rows_by_size[name]["patch"]))
    return _grouped_bar_chart_svg(
        title="StatePatch vs Full State Bytes",
        groups=grouped,
        series_names=("full_state_bytes", "patch_bytes"),
        empty_message="No state patch benchmark data found.",
    )


def _collaboration_tokens_svg(
    rows: Sequence[Mapping[str, str]],
    summary_metrics: Mapping[str, object],
) -> str:
    values = _collaboration_metric_values(
        rows,
        summary_metrics,
        "text_mode_total_tokens",
        "structured_mode_total_tokens",
    )
    return _bar_chart_svg(
        title="Collaboration Total Tokens",
        items=values,
        y_label="tokens",
        empty_message="No collaboration token data found.",
    )


def _collaboration_latency_svg(
    rows: Sequence[Mapping[str, str]],
    summary_metrics: Mapping[str, object],
) -> str:
    values = _collaboration_metric_values(
        rows,
        summary_metrics,
        "text_mode_total_latency_ms",
        "structured_mode_total_latency_ms",
    )
    return _bar_chart_svg(
        title="Collaboration Total Latency (ms)",
        items=values,
        y_label="latency_ms",
        empty_message="No collaboration latency data found.",
    )


def _memory_reuse_svg(
    rows: Sequence[Mapping[str, str]],
    summary_metrics: Mapping[str, object],
) -> str:
    memory_summary = summary_metrics.get("memory_reuse", {})
    values: List[tuple[str, float]] = []
    if isinstance(memory_summary, dict) and memory_summary:
        values = [
            ("memory_hit_rate_pct", float(memory_summary.get("memory_hit_rate", 0.0)) * 100.0),
            ("total_saved_steps", float(memory_summary.get("total_saved_steps", 0))),
        ]
    if not values and rows:
        memory_hit_count = sum(1 for row in rows if _as_bool(row.get("memory_hit", "false")))
        values = [
            ("memory_hit_rate_pct", (memory_hit_count / len(rows)) * 100.0),
            ("total_saved_steps", float(sum(_as_int(row.get("saved_steps", "0")) for row in rows))),
        ]
    return _bar_chart_svg(
        title="Memory Reuse Summary",
        items=values,
        y_label="value",
        empty_message="No memory reuse benchmark data found.",
    )


def _capability_embedding_svg(
    rows: Sequence[Mapping[str, str]],
    summary_metrics: Mapping[str, object],
) -> str:
    collaboration = summary_metrics.get("collaboration", {})
    values: List[tuple[str, float]] = []
    if isinstance(collaboration, dict) and collaboration:
        values = [
            (
                "capability_discovery_count",
                float(collaboration.get("capability_discovery_count", 0)),
            ),
            ("embedding_state_count", float(collaboration.get("embedding_state_count", 0))),
        ]
    if not values and rows:
        structured = [row for row in rows if row.get("mode") == "structured_mode"]
        values = [
            (
                "capability_discovery_count",
                float(sum(_as_int(row.get("capability_discovery_count", "0")) for row in structured)),
            ),
            (
                "embedding_state_count",
                float(sum(_as_int(row.get("embedding_state_count", "0")) for row in structured)),
            ),
        ]
    return _bar_chart_svg(
        title="Capability Discovery and Embedding Counts",
        items=values,
        y_label="count",
        empty_message="No capability or embedding count data found.",
    )


def _collaboration_metric_values(
    rows: Sequence[Mapping[str, str]],
    summary_metrics: Mapping[str, object],
    text_key: str,
    structured_key: str,
) -> List[tuple[str, float]]:
    collaboration = summary_metrics.get("collaboration", {})
    if isinstance(collaboration, dict) and collaboration:
        return [
            ("text_mode", float(collaboration.get(text_key, 0.0))),
            ("structured_mode", float(collaboration.get(structured_key, 0.0))),
        ]
    if not rows:
        return []
    text_total = 0.0
    structured_total = 0.0
    field_name = "approx_tokens" if "tokens" in text_key else "total_latency_ms"
    for row in rows:
        if row.get("mode") == "text_mode":
            text_total += _as_float(row.get(field_name, "0"))
        elif row.get("mode") == "structured_mode":
            structured_total += _as_float(row.get(field_name, "0"))
    return [("text_mode", text_total), ("structured_mode", structured_total)]


def _bar_chart_svg(
    title: str,
    items: Sequence[tuple[str, float]],
    y_label: str,
    empty_message: str,
) -> str:
    width = 860
    height = 520
    margin_left = 90
    margin_right = 40
    margin_top = 70
    margin_bottom = 90
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fffdf8"/>',
        f'<text x="{width / 2:.1f}" y="34" text-anchor="middle" font-size="22" font-family="monospace" fill="#1f2933">{_xml_escape(title)}</text>',
    ]

    if not items:
        lines.extend(
            [
                f'<rect x="{margin_left}" y="{margin_top}" width="{plot_width}" height="{plot_height}" fill="#f5f7fa" stroke="#cbd2d9"/>',
                f'<text x="{width / 2:.1f}" y="{height / 2:.1f}" text-anchor="middle" font-size="18" font-family="monospace" fill="#52606d">{_xml_escape(empty_message)}</text>',
                "</svg>",
            ]
        )
        return "\n".join(lines)

    max_value = max(value for _, value in items)
    max_value = max(max_value, 1.0)
    tick_count = 5
    scale_max = _round_up(max_value)

    lines.append(f'<text x="26" y="{margin_top + (plot_height / 2):.1f}" transform="rotate(-90 26 {margin_top + (plot_height / 2):.1f})" text-anchor="middle" font-size="14" font-family="monospace" fill="#334e68">{_xml_escape(y_label)}</text>')
    lines.append(
        f'<line x1="{margin_left}" y1="{margin_top + plot_height}" x2="{width - margin_right}" y2="{margin_top + plot_height}" stroke="#7b8794" stroke-width="1"/>'
    )
    lines.append(
        f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_height}" stroke="#7b8794" stroke-width="1"/>'
    )

    for tick in range(tick_count + 1):
        ratio = tick / float(tick_count)
        y = margin_top + plot_height - (ratio * plot_height)
        tick_value = scale_max * ratio
        lines.append(
            f'<line x1="{margin_left}" y1="{y:.1f}" x2="{width - margin_right}" y2="{y:.1f}" stroke="#e4e7eb" stroke-width="1"/>'
        )
        lines.append(
            f'<text x="{margin_left - 10}" y="{y + 5:.1f}" text-anchor="end" font-size="12" font-family="monospace" fill="#52606d">{_format_value(tick_value)}</text>'
        )

    slot_width = plot_width / float(len(items))
    bar_width = min(90.0, slot_width * 0.55)
    colors = ["#0b7285", "#f08c00", "#5c940d", "#c2255c", "#6741d9", "#1c7ed6"]
    for index, (label, value) in enumerate(items):
        x = margin_left + (slot_width * index) + ((slot_width - bar_width) / 2.0)
        bar_height = 0.0 if scale_max <= 0 else (value / scale_max) * plot_height
        y = margin_top + plot_height - bar_height
        color = colors[index % len(colors)]
        lines.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" fill="{color}" rx="4"/>'
        )
        lines.append(
            f'<text x="{x + (bar_width / 2):.1f}" y="{y - 8:.1f}" text-anchor="middle" font-size="12" font-family="monospace" fill="#102a43">{_format_value(value)}</text>'
        )
        lines.append(
            f'<text x="{x + (bar_width / 2):.1f}" y="{margin_top + plot_height + 22:.1f}" text-anchor="middle" font-size="12" font-family="monospace" fill="#334e68">{_xml_escape(label)}</text>'
        )

    lines.append("</svg>")
    return "\n".join(lines)


def _grouped_bar_chart_svg(
    title: str,
    groups: Sequence[tuple[str, float, float]],
    series_names: tuple[str, str],
    empty_message: str,
) -> str:
    width = 860
    height = 520
    margin_left = 90
    margin_right = 40
    margin_top = 70
    margin_bottom = 90
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fffdf8"/>',
        f'<text x="{width / 2:.1f}" y="34" text-anchor="middle" font-size="22" font-family="monospace" fill="#1f2933">{_xml_escape(title)}</text>',
    ]
    if not groups:
        lines.extend(
            [
                f'<rect x="{margin_left}" y="{margin_top}" width="{plot_width}" height="{plot_height}" fill="#f5f7fa" stroke="#cbd2d9"/>',
                f'<text x="{width / 2:.1f}" y="{height / 2:.1f}" text-anchor="middle" font-size="18" font-family="monospace" fill="#52606d">{_xml_escape(empty_message)}</text>',
                "</svg>",
            ]
        )
        return "\n".join(lines)

    max_value = max(max(full, patch) for _, full, patch in groups)
    scale_max = _round_up(max(max_value, 1.0))
    tick_count = 5
    lines.append(
        f'<line x1="{margin_left}" y1="{margin_top + plot_height}" x2="{width - margin_right}" y2="{margin_top + plot_height}" stroke="#7b8794" stroke-width="1"/>'
    )
    lines.append(
        f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_height}" stroke="#7b8794" stroke-width="1"/>'
    )
    for tick in range(tick_count + 1):
        ratio = tick / float(tick_count)
        y = margin_top + plot_height - (ratio * plot_height)
        tick_value = scale_max * ratio
        lines.append(
            f'<line x1="{margin_left}" y1="{y:.1f}" x2="{width - margin_right}" y2="{y:.1f}" stroke="#e4e7eb" stroke-width="1"/>'
        )
        lines.append(
            f'<text x="{margin_left - 10}" y="{y + 5:.1f}" text-anchor="end" font-size="12" font-family="monospace" fill="#52606d">{_format_value(tick_value)}</text>'
        )

    slot_width = plot_width / float(len(groups))
    bar_width = min(42.0, slot_width * 0.22)
    colors = ["#1c7ed6", "#e8590c"]
    for index, (name, first_value, second_value) in enumerate(groups):
        x_base = margin_left + (slot_width * index)
        positions = [
            x_base + (slot_width * 0.28) - (bar_width / 2.0),
            x_base + (slot_width * 0.62) - (bar_width / 2.0),
        ]
        for series_index, value in enumerate((first_value, second_value)):
            bar_height = (value / scale_max) * plot_height if scale_max > 0 else 0.0
            y = margin_top + plot_height - bar_height
            x = positions[series_index]
            lines.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" fill="{colors[series_index]}" rx="4"/>'
            )
            lines.append(
                f'<text x="{x + (bar_width / 2):.1f}" y="{y - 8:.1f}" text-anchor="middle" font-size="11" font-family="monospace" fill="#102a43">{_format_value(value)}</text>'
            )
        lines.append(
            f'<text x="{x_base + (slot_width / 2.0):.1f}" y="{margin_top + plot_height + 22:.1f}" text-anchor="middle" font-size="12" font-family="monospace" fill="#334e68">{_xml_escape(name)}</text>'
        )

    legend_y = height - 28
    lines.append(f'<rect x="{margin_left}" y="{legend_y - 12}" width="14" height="14" fill="{colors[0]}"/>')
    lines.append(f'<text x="{margin_left + 20}" y="{legend_y}" font-size="12" font-family="monospace" fill="#334e68">{_xml_escape(series_names[0])}</text>')
    lines.append(f'<rect x="{margin_left + 220}" y="{legend_y - 12}" width="14" height="14" fill="{colors[1]}"/>')
    lines.append(f'<text x="{margin_left + 240}" y="{legend_y}" font-size="12" font-family="monospace" fill="#334e68">{_xml_escape(series_names[1])}</text>')
    lines.append("</svg>")
    return "\n".join(lines)


def _round_up(value: float) -> float:
    if value <= 0:
        return 1.0
    magnitude = 10 ** max(0, int(math.floor(math.log10(value))))
    scaled = value / float(magnitude)
    rounded = math.ceil(scaled)
    return rounded * magnitude


def _format_value(value: float) -> str:
    if value >= 1000:
        return str(int(round(value)))
    if value >= 100:
        return f"{value:.0f}"
    if value >= 10:
        return f"{value:.1f}"
    return f"{value:.2f}"


def _write_svg(path: str, content: str) -> str:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


def _xml_escape(text: object) -> str:
    value = str(text)
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _as_int(value: str) -> int:
    return int(float(value))


def _as_float(value: str) -> float:
    return float(value)


def _as_bool(value: str) -> bool:
    return str(value).strip().lower() == "true"


def main() -> int:
    try:
        result = generate_result_figures()
    except Exception as exc:
        print(f"figure generation failed: {exc}", file=sys.stderr)
        return 1
    for path in result["generated_files"]:
        print(path)
    for warning in result["warnings"]:
        print(f"warning: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
