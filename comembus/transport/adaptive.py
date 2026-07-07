"""Adaptive transport policy for CoMemBus."""

from __future__ import annotations

from dataclasses import dataclass


DIRECT_UDS = "direct_uds"
SHM_REF = "shm_ref"


@dataclass(frozen=True)
class AdaptiveTransportPolicy:
    """Choose a transport mode from message size and receiver fan-out."""

    direct_threshold_bytes: int = 65536
    prefer_shm_when_receivers_gt: int = 1

    def __post_init__(self) -> None:
        if self.direct_threshold_bytes <= 0:
            raise ValueError("direct_threshold_bytes must be positive")
        if self.prefer_shm_when_receivers_gt < 0:
            raise ValueError("prefer_shm_when_receivers_gt must be non-negative")

    def choose_mode(self, size_bytes: int, receivers: int) -> str:
        if size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")
        if receivers <= 0:
            raise ValueError("receivers must be positive")
        if (
            size_bytes < self.direct_threshold_bytes
            and receivers <= self.prefer_shm_when_receivers_gt
        ):
            return DIRECT_UDS
        return SHM_REF

