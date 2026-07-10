"""Deterministic named failure points for recovery tests and benchmarks."""

from __future__ import annotations

import threading
from typing import Dict, Mapping


class InjectedFailure(RuntimeError):
    def __init__(self, failure_point: str) -> None:
        self.failure_point = failure_point
        super().__init__(f"injected failure at {failure_point}")


class FailureInjector:
    """Raise a visible exception a configured number of times per failure point."""

    def __init__(self, failures: Mapping[str, int] | None = None) -> None:
        self._lock = threading.Lock()
        self._remaining: Dict[str, int] = {}
        for name, count in dict(failures or {}).items():
            self.configure(name, count)

    def configure(self, failure_point: str, count: int = 1) -> None:
        if not isinstance(failure_point, str) or not failure_point:
            raise ValueError("failure_point must be a non-empty string")
        if not isinstance(count, int) or count < 0:
            raise ValueError("count must be a non-negative integer")
        with self._lock:
            self._remaining[failure_point] = count

    def trigger(self, failure_point: str) -> None:
        if not isinstance(failure_point, str) or not failure_point:
            raise ValueError("failure_point must be a non-empty string")
        with self._lock:
            remaining = self._remaining.get(failure_point, 0)
            if remaining <= 0:
                return
            self._remaining[failure_point] = remaining - 1
        raise InjectedFailure(failure_point)

    def remaining(self, failure_point: str) -> int:
        with self._lock:
            return self._remaining.get(failure_point, 0)
