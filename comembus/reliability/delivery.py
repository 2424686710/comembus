"""ACK/NACK delivery queues with visibility timeout, dedup, and backpressure."""

from __future__ import annotations

import copy
from collections import defaultdict, deque
from dataclasses import dataclass
import threading
import time
import uuid
from typing import Any, Callable, Deque, Dict, Optional, Set

from .dedup import DedupStore


class ReliabilityError(Exception):
    """Base reliable delivery error."""


class QueueFullError(ReliabilityError):
    """Raised when publishing would exceed configured queue capacity."""


class MessageNotFoundError(ReliabilityError):
    """Raised for ACK/NACK/renew operations on an unknown message."""


@dataclass
class DeliveryEnvelope:
    message_id: str
    topic: str
    payload: Dict[str, Any]
    delivery_attempt: int
    created_at: float
    visibility_deadline: Optional[float]
    consumer_agent: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "topic": self.topic,
            "payload": copy.deepcopy(self.payload),
            "delivery_attempt": self.delivery_attempt,
            "created_at": self.created_at,
            "visibility_deadline": self.visibility_deadline,
            "consumer_agent": self.consumer_agent,
        }


class ReliableDeliveryManager:
    """In-memory at-least-once queue with explicit completion semantics."""

    def __init__(
        self,
        max_queue_size: int = 0,
        visibility_timeout: float = 30.0,
        dedup_store: Optional[DedupStore] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_queue_size < 0:
            raise ValueError("max_queue_size must be non-negative")
        if visibility_timeout <= 0:
            raise ValueError("visibility_timeout must be positive")
        self.max_queue_size = max_queue_size
        self.visibility_timeout = float(visibility_timeout)
        self.dedup_store = dedup_store or DedupStore()
        self._clock = clock
        self._lock = threading.RLock()
        self._available: Dict[str, Deque[DeliveryEnvelope]] = defaultdict(deque)
        self._invisible: Dict[str, DeliveryEnvelope] = {}
        self._known_message_ids: Set[str] = set()
        self._requeued_count = 0
        self._published_count = 0
        self._acked_count = 0

    def publish(
        self,
        topic: str,
        payload: Dict[str, Any],
        message_id: Optional[str] = None,
        created_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        _validate_topic(topic)
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dict")
        resolved_id = message_id or uuid.uuid4().hex
        if not isinstance(resolved_id, str) or not resolved_id:
            raise ValueError("message_id must be a non-empty string")
        with self._lock:
            processed = self.dedup_store.get(resolved_id)
            if processed is not None:
                self.dedup_store.note_duplicate()
                return {
                    "topic": topic,
                    "queued": self._queue_depth_locked(),
                    "message_id": resolved_id,
                    "duplicate_suppressed": True,
                    "processed": True,
                    "processed_result": processed.result,
                }
            if resolved_id in self._known_message_ids:
                self.dedup_store.note_duplicate()
                return {
                    "topic": topic,
                    "queued": self._queue_depth_locked(),
                    "message_id": resolved_id,
                    "duplicate_suppressed": True,
                    "processed": False,
                    "processed_result": None,
                }
            if self.max_queue_size and self._queue_depth_locked() >= self.max_queue_size:
                raise QueueFullError(
                    f"queue capacity exceeded: max_queue_size={self.max_queue_size}"
                )
            envelope = DeliveryEnvelope(
                message_id=resolved_id,
                topic=topic,
                payload=copy.deepcopy(payload),
                delivery_attempt=0,
                created_at=self._clock() if created_at is None else float(created_at),
                visibility_deadline=None,
            )
            self._available[topic].append(envelope)
            self._known_message_ids.add(resolved_id)
            self._published_count += 1
            return {
                "topic": topic,
                "queued": self._queue_depth_locked(),
                "message_id": resolved_id,
                "duplicate_suppressed": False,
                "processed": False,
                "processed_result": None,
            }

    def poll(
        self,
        topic: str,
        consumer_agent: str = "",
        visibility_timeout: Optional[float] = None,
        auto_ack: bool = False,
    ) -> Optional[DeliveryEnvelope]:
        _validate_topic(topic)
        timeout = self.visibility_timeout if visibility_timeout is None else float(
            visibility_timeout
        )
        if timeout <= 0:
            raise ValueError("visibility_timeout must be positive")
        with self._lock:
            self._requeue_expired_locked(self._clock())
            queue = self._available[topic]
            if not queue:
                return None
            envelope = queue.popleft()
            envelope.delivery_attempt += 1
            envelope.consumer_agent = str(consumer_agent or "")
            envelope.visibility_deadline = self._clock() + timeout
            self._invisible[envelope.message_id] = envelope
            returned = _clone_envelope(envelope)
            if auto_ack:
                self._ack_locked(envelope.message_id, result=None)
            return returned

    def ack(self, message_id: str, result: Any = None) -> Dict[str, Any]:
        _validate_message_id(message_id)
        with self._lock:
            if message_id not in self._invisible:
                processed = self.dedup_store.get(message_id)
                if processed is not None:
                    return {
                        "message_id": message_id,
                        "acked": True,
                        "already_acked": True,
                        "processed_result": processed.result,
                    }
                raise MessageNotFoundError(f"invisible message not found: {message_id}")
            processed = self._ack_locked(message_id, result)
            return {
                "message_id": message_id,
                "acked": True,
                "already_acked": False,
                "processed_result": processed.result,
            }

    def nack(self, message_id: str) -> Dict[str, Any]:
        _validate_message_id(message_id)
        with self._lock:
            envelope = self._invisible.pop(message_id, None)
            if envelope is None:
                raise MessageNotFoundError(f"invisible message not found: {message_id}")
            envelope.visibility_deadline = None
            envelope.consumer_agent = ""
            self._available[envelope.topic].appendleft(envelope)
            self._requeued_count += 1
            return {"message_id": message_id, "nacked": True, "requeued": True}

    def renew_visibility(
        self,
        message_id: str,
        visibility_timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        _validate_message_id(message_id)
        timeout = self.visibility_timeout if visibility_timeout is None else float(
            visibility_timeout
        )
        if timeout <= 0:
            raise ValueError("visibility_timeout must be positive")
        with self._lock:
            self._requeue_expired_locked(self._clock())
            envelope = self._invisible.get(message_id)
            if envelope is None:
                raise MessageNotFoundError(f"invisible message not found: {message_id}")
            envelope.visibility_deadline = self._clock() + timeout
            return {
                "message_id": message_id,
                "renewed": True,
                "visibility_deadline": envelope.visibility_deadline,
            }

    def requeue_expired(self) -> int:
        with self._lock:
            return self._requeue_expired_locked(self._clock())

    def get_stats(self) -> Dict[str, int]:
        with self._lock:
            self._requeue_expired_locked(self._clock())
            return {
                "queued_message_count": self._queue_depth_locked(),
                "available_message_count": sum(
                    len(queue) for queue in self._available.values()
                ),
                "invisible_message_count": len(self._invisible),
                "published_message_count": self._published_count,
                "acked_message_count": self._acked_count,
                "message_requeued_count": self._requeued_count,
                **self.dedup_store.get_stats(),
            }

    def _ack_locked(self, message_id: str, result: Any) -> Any:
        self._invisible.pop(message_id)
        self._known_message_ids.discard(message_id)
        processed = self.dedup_store.record_processed(message_id, result=result)
        self._acked_count += 1
        return processed

    def _requeue_expired_locked(self, now: float) -> int:
        expired = [
            message_id
            for message_id, envelope in self._invisible.items()
            if envelope.visibility_deadline is not None
            and envelope.visibility_deadline <= now
        ]
        for message_id in expired:
            envelope = self._invisible.pop(message_id)
            envelope.visibility_deadline = None
            envelope.consumer_agent = ""
            self._available[envelope.topic].appendleft(envelope)
            self._requeued_count += 1
        return len(expired)

    def _queue_depth_locked(self) -> int:
        return sum(len(queue) for queue in self._available.values()) + len(
            self._invisible
        )


def _clone_envelope(value: DeliveryEnvelope) -> DeliveryEnvelope:
    return DeliveryEnvelope(
        message_id=value.message_id,
        topic=value.topic,
        payload=copy.deepcopy(value.payload),
        delivery_attempt=value.delivery_attempt,
        created_at=value.created_at,
        visibility_deadline=value.visibility_deadline,
        consumer_agent=value.consumer_agent,
    )


def _validate_topic(topic: str) -> None:
    if not isinstance(topic, str) or not topic:
        raise ValueError("topic must be a non-empty string")


def _validate_message_id(message_id: str) -> None:
    if not isinstance(message_id, str) or not message_id:
        raise ValueError("message_id must be a non-empty string")
