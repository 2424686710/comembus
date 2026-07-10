"""Per-run process measurements backed by ``time`` and ``resource``."""

from __future__ import annotations

from dataclasses import dataclass
import resource
import sys
import time
from typing import Optional


@dataclass(frozen=True)
class ProcessSnapshot:
    cpu_time_seconds: float
    peak_rss_kb: int
    voluntary_context_switches: Optional[int]
    involuntary_context_switches: Optional[int]


@dataclass(frozen=True)
class ProcessUsage:
    cpu_time_ms: float
    peak_rss_kb: int
    voluntary_context_switches: Optional[int]
    involuntary_context_switches: Optional[int]

    def to_dict(self) -> dict[str, int | float | None]:
        return {
            "cpu_time_ms": self.cpu_time_ms,
            "peak_rss_kb": self.peak_rss_kb,
            "voluntary_context_switches": self.voluntary_context_switches,
            "involuntary_context_switches": self.involuntary_context_switches,
        }


def capture_process_snapshot() -> ProcessSnapshot:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    max_rss = int(usage.ru_maxrss)
    # Linux/openEuler report KiB; macOS reports bytes.
    if sys.platform == "darwin":
        max_rss //= 1024
    return ProcessSnapshot(
        cpu_time_seconds=time.process_time(),
        peak_rss_kb=max_rss,
        voluntary_context_switches=_optional_usage_value(usage, "ru_nvcsw"),
        involuntary_context_switches=_optional_usage_value(usage, "ru_nivcsw"),
    )


def usage_between(start: ProcessSnapshot, end: ProcessSnapshot) -> ProcessUsage:
    return ProcessUsage(
        cpu_time_ms=max(0.0, (end.cpu_time_seconds - start.cpu_time_seconds) * 1000.0),
        peak_rss_kb=end.peak_rss_kb,
        voluntary_context_switches=_optional_delta(
            start.voluntary_context_switches, end.voluntary_context_switches
        ),
        involuntary_context_switches=_optional_delta(
            start.involuntary_context_switches, end.involuntary_context_switches
        ),
    )


class ProcessMetrics:
    """Context manager for measuring CPU, peak RSS, and context switches."""

    def __init__(self) -> None:
        self._start: Optional[ProcessSnapshot] = None
        self.usage: Optional[ProcessUsage] = None

    def start(self) -> "ProcessMetrics":
        self._start = capture_process_snapshot()
        self.usage = None
        return self

    def stop(self) -> ProcessUsage:
        if self._start is None:
            raise RuntimeError("process metrics have not been started")
        self.usage = usage_between(self._start, capture_process_snapshot())
        self._start = None
        return self.usage

    def __enter__(self) -> "ProcessMetrics":
        return self.start()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()


def _optional_usage_value(usage: resource.struct_rusage, name: str) -> Optional[int]:
    value = getattr(usage, name, None)
    return int(value) if value is not None else None


def _optional_delta(start: Optional[int], end: Optional[int]) -> Optional[int]:
    if start is None or end is None:
        return None
    return max(0, end - start)
