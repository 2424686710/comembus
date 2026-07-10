"""Adaptive transport policy for CoMemBus."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping, Optional


DIRECT_UDS = "direct_uds"
SHM_REF = "shm_ref"


@dataclass(frozen=True)
class AdaptiveTransportPolicy:
    """Choose a transport mode from message size and receiver fan-out."""

    direct_threshold_bytes: int = 65536
    prefer_shm_when_receivers_gt: int = 1
    receiver_thresholds: Optional[Mapping[int, int]] = None

    def __post_init__(self) -> None:
        if self.direct_threshold_bytes <= 0:
            raise ValueError("direct_threshold_bytes must be positive")
        if self.prefer_shm_when_receivers_gt < 0:
            raise ValueError("prefer_shm_when_receivers_gt must be non-negative")
        if self.receiver_thresholds is not None:
            normalized = {
                int(receivers): int(threshold)
                for receivers, threshold in self.receiver_thresholds.items()
            }
            if any(receivers <= 0 for receivers in normalized):
                raise ValueError("receiver threshold keys must be positive")
            if any(threshold <= 0 for threshold in normalized.values()):
                raise ValueError("receiver thresholds must be positive")
            object.__setattr__(self, "receiver_thresholds", normalized)

    def choose_mode(self, size_bytes: int, receivers: int) -> str:
        if size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")
        if receivers <= 0:
            raise ValueError("receivers must be positive")
        if self.receiver_thresholds:
            threshold = self._threshold_for_receivers(receivers)
            return DIRECT_UDS if size_bytes < threshold else SHM_REF
        if (
            size_bytes < self.direct_threshold_bytes
            and receivers <= self.prefer_shm_when_receivers_gt
        ):
            return DIRECT_UDS
        return SHM_REF

    def _threshold_for_receivers(self, receivers: int) -> int:
        thresholds = self.receiver_thresholds or {}
        if receivers in thresholds:
            return thresholds[receivers]
        ordered = sorted(thresholds)
        for receiver_count in ordered:
            if receiver_count >= receivers:
                return thresholds[receiver_count]
        if ordered:
            return thresholds[ordered[-1]]
        return self.direct_threshold_bytes

    @classmethod
    def from_profile(
        cls,
        profile_path: str | Path,
        fallback_on_error: bool = True,
    ) -> "AdaptiveTransportPolicy":
        """Load calibrated thresholds, retaining the fixed 64 KiB fallback."""

        try:
            with Path(profile_path).open("r", encoding="utf-8") as handle:
                profile = json.load(handle)
            raw_thresholds = profile["thresholds_by_receivers"]
            if not isinstance(raw_thresholds, dict) or not raw_thresholds:
                raise ValueError("profile thresholds_by_receivers must be a non-empty object")
            thresholds = {
                int(receivers): int(threshold)
                for receivers, threshold in raw_thresholds.items()
            }
            return cls(receiver_thresholds=thresholds)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            if fallback_on_error:
                return cls()
            raise
