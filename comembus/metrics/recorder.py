"""Thread-safe counters for real transport and shared-memory activity."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Dict


@dataclass(frozen=True)
class MetricsSnapshot:
    """Immutable point-in-time values from :class:`MetricsRecorder`."""

    sent_bytes: int = 0
    received_bytes: int = 0
    message_count: int = 0
    shm_bytes_written: int = 0
    shm_bytes_read: int = 0

    @property
    def wire_bytes(self) -> int:
        """Bytes put on the wire, counted once at the sending endpoint."""

        return self.sent_bytes

    def to_dict(self) -> Dict[str, int]:
        return {
            "sent_bytes": self.sent_bytes,
            "received_bytes": self.received_bytes,
            "message_count": self.message_count,
            "shm_bytes_written": self.shm_bytes_written,
            "shm_bytes_read": self.shm_bytes_read,
            "wire_bytes": self.wire_bytes,
        }

    def __sub__(self, other: "MetricsSnapshot") -> "MetricsSnapshot":
        return MetricsSnapshot(
            sent_bytes=self.sent_bytes - other.sent_bytes,
            received_bytes=self.received_bytes - other.received_bytes,
            message_count=self.message_count - other.message_count,
            shm_bytes_written=self.shm_bytes_written - other.shm_bytes_written,
            shm_bytes_read=self.shm_bytes_read - other.shm_bytes_read,
        )


class MetricsRecorder:
    """Collect exact byte counts without changing transport behavior when absent.

    ``message_count`` counts successfully sent frames. Receive observations have a
    separate byte counter so one physical frame is not reported as two wire
    transfers when both endpoints share a recorder.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sent_bytes = 0
        self._received_bytes = 0
        self._message_count = 0
        self._shm_bytes_written = 0
        self._shm_bytes_read = 0

    def record_sent(self, frame_bytes: int) -> None:
        self._validate_count(frame_bytes, "frame_bytes")
        with self._lock:
            self._sent_bytes += frame_bytes
            self._message_count += 1

    def record_received(self, frame_bytes: int) -> None:
        self._validate_count(frame_bytes, "frame_bytes")
        with self._lock:
            self._received_bytes += frame_bytes

    def record_shm_write(self, byte_count: int) -> None:
        self._validate_count(byte_count, "byte_count")
        with self._lock:
            self._shm_bytes_written += byte_count

    def record_shm_read(self, byte_count: int) -> None:
        self._validate_count(byte_count, "byte_count")
        with self._lock:
            self._shm_bytes_read += byte_count

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            return MetricsSnapshot(
                sent_bytes=self._sent_bytes,
                received_bytes=self._received_bytes,
                message_count=self._message_count,
                shm_bytes_written=self._shm_bytes_written,
                shm_bytes_read=self._shm_bytes_read,
            )

    def reset(self) -> None:
        with self._lock:
            self._sent_bytes = 0
            self._received_bytes = 0
            self._message_count = 0
            self._shm_bytes_written = 0
            self._shm_bytes_read = 0

    @property
    def sent_bytes(self) -> int:
        return self.snapshot().sent_bytes

    @property
    def received_bytes(self) -> int:
        return self.snapshot().received_bytes

    @property
    def message_count(self) -> int:
        return self.snapshot().message_count

    @property
    def shm_bytes_written(self) -> int:
        return self.snapshot().shm_bytes_written

    @property
    def shm_bytes_read(self) -> int:
        return self.snapshot().shm_bytes_read

    @staticmethod
    def _validate_count(value: int, field_name: str) -> None:
        if not isinstance(value, int):
            raise TypeError(f"{field_name} must be an integer")
        if value < 0:
            raise ValueError(f"{field_name} must be non-negative")
