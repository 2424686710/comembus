"""Thread-safe processed-message result cache used for idempotent delivery."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import threading
import time
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class ProcessedMessage:
    message_id: str
    result: Any
    processed_at: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "result": copy.deepcopy(self.result),
            "processed_at": self.processed_at,
        }


class DedupStore:
    """Remember completed message IDs and their original business result."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processed: Dict[str, ProcessedMessage] = {}
        self._duplicate_hits = 0

    def record_processed(
        self,
        message_id: str,
        result: Any = None,
        processed_at: Optional[float] = None,
    ) -> ProcessedMessage:
        self._validate_message_id(message_id)
        with self._lock:
            existing = self._processed.get(message_id)
            if existing is not None:
                return _clone_processed(existing)
            record = ProcessedMessage(
                message_id=message_id,
                result=copy.deepcopy(result),
                processed_at=time.time() if processed_at is None else float(processed_at),
            )
            self._processed[message_id] = record
            return _clone_processed(record)

    def get(self, message_id: str) -> Optional[ProcessedMessage]:
        self._validate_message_id(message_id)
        with self._lock:
            value = self._processed.get(message_id)
            return None if value is None else _clone_processed(value)

    def get_processed_result(self, message_id: str) -> Any:
        value = self.get(message_id)
        if value is None:
            return None
        return copy.deepcopy(value.result)

    def is_processed(self, message_id: str) -> bool:
        return self.get(message_id) is not None

    def note_duplicate(self) -> None:
        with self._lock:
            self._duplicate_hits += 1

    def get_stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "processed_message_count": len(self._processed),
                "duplicate_suppressed_count": self._duplicate_hits,
            }

    def clear(self) -> None:
        with self._lock:
            self._processed.clear()
            self._duplicate_hits = 0

    @staticmethod
    def _validate_message_id(message_id: str) -> None:
        if not isinstance(message_id, str) or not message_id:
            raise ValueError("message_id must be a non-empty string")


def _clone_processed(value: ProcessedMessage) -> ProcessedMessage:
    return ProcessedMessage(
        message_id=value.message_id,
        result=copy.deepcopy(value.result),
        processed_at=value.processed_at,
    )
