#!/usr/bin/env python3
"""Summarize benchmark CSV outputs into Markdown and JSON reports."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import statistics
import sys
from typing import Dict, Iterable, List, Mapping


RESULT_FILES = {
    "transport": "transport_bench.csv",
    "state_patch": "state_patch_bench.csv",
    "memory_reuse": "memory_reuse_bench.csv",
    "collaboration": "collaboration_bench.csv",
}


def summarize_result_files(
    results_dir: str = "results",
    report_path: str | None = None,
    metrics_path: str | None = None,
) -> Dict[str, object]:
    base_dir = Path(results_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    report_target = Path(report_path) if report_path else base_dir / "summary_report.md"
    metrics_target = Path(metrics_path) if metrics_path else base_dir / "summary_metrics.json"

    warnings: List[str] = []
    transport_rows = _load_rows(base_dir / RESULT_FILES["transport"], warnings)
    state_patch_rows = _load_rows(base_dir / RESULT_FILES["state_patch"], warnings)
    memory_reuse_rows = _load_rows(base_dir / RESULT_FILES["memory_reuse"], warnings)
    collaboration_rows = _load_rows(base_dir / RESULT_FILES["collaboration"], warnings)

    metrics = {
        "warnings": warnings,
        "transport": _summarize_transport(transport_rows),
        "state_patch": _summarize_state_patch(state_patch_rows),
        "memory_reuse": _summarize_memory_reuse(memory_reuse_rows),
        "collaboration": _summarize_collaboration(collaboration_rows),
    }
    report = render_summary_report(metrics)

    report_target.write_text(report, encoding="utf-8")
    metrics_target.write_text(
        json.dumps(metrics, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return metrics


def render_summary_report(metrics: Mapping[str, object]) -> str:
    warnings = list(metrics.get("warnings", []))
    transport = metrics.get("transport", {})
    state_patch = metrics.get("state_patch", {})
    memory_reuse = metrics.get("memory_reuse", {})
    collaboration = metrics.get("collaboration", {})

    lines = ["# CoMemBus Result Summary", ""]
    if warnings:
        lines.append("## Warnings")
        for warning in warnings:
            lines.append(f"- {warning}")
        lines.append("")

    lines.extend(_render_transport_section(transport))
    lines.extend(_render_state_patch_section(state_patch))
    lines.extend(_render_memory_reuse_section(memory_reuse))
    lines.extend(_render_collaboration_section(collaboration))
    lines.extend(_render_requirement_mapping(collaboration, transport, state_patch, memory_reuse))
    return "\n".join(lines).strip() + "\n"


def _load_rows(path: Path, warnings: List[str]) -> List[Dict[str, str]]:
    if not path.exists():
        warnings.append(f"missing result file: {path}")
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _summarize_transport(rows: Iterable[Mapping[str, str]]) -> Dict[str, object]:
    grouped: Dict[str, List[Mapping[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row.get("mode", ""), []).append(row)

    by_mode: Dict[str, Dict[str, float]] = {}
    for mode, mode_rows in grouped.items():
        latencies = [_as_float(row.get("latency_ms", "0")) for row in mode_rows]
        checksum_ok = [_as_bool(row.get("checksum_ok", "false")) for row in mode_rows]
        by_mode[mode] = {
            "avg_latency_ms": statistics.mean(latencies) if latencies else 0.0,
            "checksum_ok_rate": (
                sum(1 for item in checksum_ok if item) / len(checksum_ok)
                if checksum_ok
                else 0.0
            ),
        }
    return {"row_count": sum(len(items) for items in grouped.values()), "by_mode": by_mode}


def _summarize_state_patch(rows: Iterable[Mapping[str, str]]) -> Dict[str, object]:
    by_state_size: Dict[str, Dict[str, object]] = {}
    for row in rows:
        if row.get("mode") != "patch":
            continue
        state_size = row.get("state_size", "")
        by_state_size[state_size] = {
            "full_state_bytes": _as_int(row.get("full_state_bytes", "0")),
            "patch_bytes": _as_int(row.get("patch_bytes", "0")),
            "reduction_ratio": _as_float(row.get("reduction_ratio", "0")),
            "version_ok": _as_bool(row.get("version_ok", "false")),
        }
    return {"row_count": len(by_state_size), "by_state_size": by_state_size}


def _summarize_memory_reuse(rows: Iterable[Mapping[str, str]]) -> Dict[str, object]:
    row_list = list(rows)
    memory_hit_count = sum(1 for row in row_list if _as_bool(row.get("memory_hit", "false")))
    total_saved_steps = sum(_as_int(row.get("saved_steps", "0")) for row in row_list)
    return {
        "row_count": len(row_list),
        "memory_hit_count": memory_hit_count,
        "memory_hit_rate": (memory_hit_count / len(row_list)) if row_list else 0.0,
        "total_saved_steps": total_saved_steps,
    }


def _summarize_collaboration(rows: Iterable[Mapping[str, str]]) -> Dict[str, object]:
    row_list = list(rows)
    text_rows = [row for row in row_list if row.get("mode") == "text_mode"]
    structured_rows = [row for row in row_list if row.get("mode") == "structured_mode"]

    text_total_tokens = sum(_as_int(row.get("approx_tokens", "0")) for row in text_rows)
    structured_total_tokens = sum(_as_int(row.get("approx_tokens", "0")) for row in structured_rows)
    text_total_latency = sum(_as_float(row.get("total_latency_ms", "0")) for row in text_rows)
    structured_total_latency = sum(
        _as_float(row.get("total_latency_ms", "0")) for row in structured_rows
    )
    memory_hit_count = sum(
        1 for row in structured_rows if _as_bool(row.get("memory_hit", "false"))
    )
    total_saved_steps = sum(_as_int(row.get("saved_steps", "0")) for row in structured_rows)
    embedding_state_count = sum(
        _as_int(row.get("embedding_state_count", "0")) for row in structured_rows
    )
    capability_discovery_count = sum(
        _as_int(row.get("capability_discovery_count", "0")) for row in structured_rows
    )

    return {
        "row_count": len(row_list),
        "text_mode_total_tokens": text_total_tokens,
        "structured_mode_total_tokens": structured_total_tokens,
        "token_saving_ratio": (
            (text_total_tokens - structured_total_tokens) / text_total_tokens
            if text_total_tokens
            else 0.0
        ),
        "text_mode_total_latency_ms": text_total_latency,
        "structured_mode_total_latency_ms": structured_total_latency,
        "latency_saving_ratio": (
            (text_total_latency - structured_total_latency) / text_total_latency
            if text_total_latency
            else 0.0
        ),
        "structured_mode_memory_hit_count": memory_hit_count,
        "structured_mode_memory_hit_rate": (
            memory_hit_count / len(structured_rows) if structured_rows else 0.0
        ),
        "total_saved_steps": total_saved_steps,
        "embedding_state_count": embedding_state_count,
        "capability_discovery_count": capability_discovery_count,
        "scenario_families": sorted(
            {row.get("scenario_family", "") for row in structured_rows if row.get("scenario_family")}
        ),
    }


def _render_transport_section(summary: Mapping[str, object]) -> List[str]:
    lines = ["## Transport Benchmark Summary"]
    by_mode = summary.get("by_mode", {})
    if not isinstance(by_mode, dict) or not by_mode:
        lines.append("- No transport benchmark data found.")
        lines.append("")
        return lines
    for mode in ("direct_uds", "shm_ref", "adaptive"):
        metrics = by_mode.get(mode)
        if not isinstance(metrics, dict):
            continue
        lines.append(
            f"- {mode}: avg_latency_ms={float(metrics['avg_latency_ms']):.3f}, "
            f"checksum_ok_rate={float(metrics['checksum_ok_rate']):.4f}"
        )
    lines.append("")
    return lines


def _render_state_patch_section(summary: Mapping[str, object]) -> List[str]:
    lines = ["## StatePatch Benchmark Summary"]
    by_state_size = summary.get("by_state_size", {})
    if not isinstance(by_state_size, dict) or not by_state_size:
        lines.append("- No state patch benchmark data found.")
        lines.append("")
        return lines
    for state_size in ("small", "medium", "large"):
        metrics = by_state_size.get(state_size)
        if not isinstance(metrics, dict):
            continue
        lines.append(
            f"- {state_size}: full_state_bytes={int(metrics['full_state_bytes'])}, "
            f"patch_bytes={int(metrics['patch_bytes'])}, "
            f"reduction_ratio={float(metrics['reduction_ratio']):.6f}"
        )
    lines.append("")
    return lines


def _render_memory_reuse_section(summary: Mapping[str, object]) -> List[str]:
    lines = ["## Memory Reuse Benchmark Summary"]
    row_count = int(summary.get("row_count", 0))
    if row_count == 0:
        lines.append("- No memory reuse benchmark data found.")
        lines.append("")
        return lines
    lines.append(f"- memory_hit_count={int(summary['memory_hit_count'])}")
    lines.append(f"- memory_hit_rate={float(summary['memory_hit_rate']):.4f}")
    lines.append(f"- total_saved_steps={int(summary['total_saved_steps'])}")
    lines.append("")
    return lines


def _render_collaboration_section(summary: Mapping[str, object]) -> List[str]:
    lines = ["## Collaboration Mode Benchmark Summary"]
    row_count = int(summary.get("row_count", 0))
    if row_count == 0:
        lines.append("- No collaboration benchmark data found.")
        lines.append("")
        return lines
    lines.append(f"- text_mode total_tokens={int(summary['text_mode_total_tokens'])}")
    lines.append(
        f"- structured_mode total_tokens={int(summary['structured_mode_total_tokens'])}"
    )
    lines.append(f"- token_saving_ratio={float(summary['token_saving_ratio']):.6f}")
    lines.append(f"- latency_saving_ratio={float(summary['latency_saving_ratio']):.6f}")
    lines.append(
        f"- structured_mode memory_hit_rate={float(summary['structured_mode_memory_hit_rate']):.4f}"
    )
    lines.append(f"- total_saved_steps={int(summary['total_saved_steps'])}")
    lines.append(f"- embedding_state_count={int(summary['embedding_state_count'])}")
    lines.append(
        f"- capability_discovery_count={int(summary['capability_discovery_count'])}"
    )
    scenario_families = summary.get("scenario_families", [])
    if isinstance(scenario_families, list) and scenario_families:
        lines.append(f"- scenario_families={','.join(scenario_families)}")
    lines.append("")
    return lines


def _render_requirement_mapping(
    collaboration: Mapping[str, object],
    transport: Mapping[str, object],
    state_patch: Mapping[str, object],
    memory_reuse: Mapping[str, object],
) -> List[str]:
    lines = ["## Competition Requirement Mapping"]
    lines.append("- 低开销通信: transport benchmark 对比 direct_uds、shm_ref、adaptive。")
    lines.append("- 非文本状态传递: StatePatch 与 embedding_state 记录结构化状态和语义向量。")
    lines.append("- 共享记忆复用: memory reuse benchmark 统计 memory_hit_rate 与 total_saved_steps。")
    lines.append("- 纯文本 vs 结构化协议对比: collaboration benchmark 统计 token_saving_ratio。")
    lines.append(
        f"- 10 轮连续任务: 当前 collaboration rows={int(collaboration.get('row_count', 0))}。"
    )
    lines.append("")
    return lines


def _as_int(value: str) -> int:
    return int(float(value))


def _as_float(value: str) -> float:
    return float(value)


def _as_bool(value: str) -> bool:
    return str(value).strip().lower() == "true"


def main() -> int:
    try:
        metrics = summarize_result_files()
    except Exception as exc:
        print(f"result summary failed: {exc}", file=sys.stderr)
        return 1

    print("wrote results/summary_report.md")
    print("wrote results/summary_metrics.json")
    print(f"warnings={len(metrics.get('warnings', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
