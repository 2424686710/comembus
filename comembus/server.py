"""In-memory CoMemBus server for the stage-1 MVP."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional, Set

from .metrics.recorder import MetricsRecorder
from .protocol import Message, ProtocolError
from .reliability.dedup import DedupStore
from .reliability.delivery import ReliabilityError, ReliableDeliveryManager
from .transport.uds import UnixDomainSocketServer


class AgentBusServer:
    """Small UDS-based message bus server."""

    def __init__(
        self,
        socket_path: str,
        metrics_recorder: Optional[MetricsRecorder] = None,
        max_queue_size: int = 0,
        visibility_timeout: float = 30.0,
        dedup_store: Optional[DedupStore] = None,
    ) -> None:
        self.socket_path = socket_path
        self._lock = threading.Lock()
        self._registered_agents: Set[str] = set()
        self.delivery_manager = ReliableDeliveryManager(
            max_queue_size=max_queue_size,
            visibility_timeout=visibility_timeout,
            dedup_store=dedup_store,
        )
        self._uds_server = UnixDomainSocketServer(
            socket_path,
            self._handle_request,
            metrics_recorder=metrics_recorder,
        )
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._uds_server.start()
        self._running = True

    def stop(self) -> None:
        if not self._running:
            return
        self._uds_server.stop()
        self._running = False

    def _handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        try:
            message = Message.from_dict(request)
            if message.type == "register":
                data = self._register(message.payload)
            elif message.type == "publish":
                data = self._publish(message.topic, message.payload, message)
            elif message.type == "poll":
                data = self._poll(message.topic, message.payload)
            elif message.type == "ack":
                data = self._ack(message.payload)
            elif message.type == "nack":
                data = self._nack(message.payload)
            elif message.type == "renew_visibility":
                data = self._renew_visibility(message.payload)
            elif message.type == "ping":
                data = "pong"
            elif message.type == "shutdown":
                data = self._shutdown()
            else:
                raise ValueError(f"unsupported command: {message.type}")
            return {"ok": True, "data": data}
        except (ProtocolError, ValueError, TypeError) as exc:
            return {
                "ok": False,
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
        except ReliabilityError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "error_type": type(exc).__name__,
            }

    def _register(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = payload.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("agent_id must be a non-empty string")
        with self._lock:
            self._registered_agents.add(agent_id)
        return {"agent_id": agent_id}

    def _publish(
        self,
        topic: Optional[str],
        payload: Dict[str, Any],
        message: Message,
    ) -> Dict[str, Any]:
        if not isinstance(topic, str) or not topic:
            raise ValueError("topic must be a non-empty string")
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        return self.delivery_manager.publish(
            topic=topic,
            payload=payload,
            message_id=message.message_id,
            created_at=message.created_at,
        )

    def _poll(
        self,
        topic: Optional[str],
        options: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(topic, str) or not topic:
            raise ValueError("topic must be a non-empty string")
        auto_ack = bool(options.get("auto_ack", True))
        consumer_agent = str(options.get("consumer_agent", ""))
        timeout_value = options.get("visibility_timeout")
        timeout = None if timeout_value is None else float(timeout_value)
        envelope = self.delivery_manager.poll(
            topic,
            consumer_agent=consumer_agent,
            visibility_timeout=timeout,
            auto_ack=auto_ack,
        )
        if envelope is None:
            return None
        return envelope.payload if auto_ack else envelope.to_dict()

    def _ack(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.delivery_manager.ack(
            _required_message_id(payload), result=payload.get("result")
        )

    def _nack(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.delivery_manager.nack(_required_message_id(payload))

    def _renew_visibility(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        timeout_value = payload.get("visibility_timeout")
        timeout = None if timeout_value is None else float(timeout_value)
        return self.delivery_manager.renew_visibility(
            _required_message_id(payload), visibility_timeout=timeout
        )

    def _shutdown(self) -> str:
        threading.Thread(target=self._delayed_stop, daemon=True).start()
        return "shutting down"

    def _delayed_stop(self) -> None:
        time.sleep(0.05)
        self.stop()


def _required_message_id(payload: Dict[str, Any]) -> str:
    message_id = payload.get("message_id")
    if not isinstance(message_id, str) or not message_id:
        raise ValueError("message_id must be a non-empty string")
    return message_id
