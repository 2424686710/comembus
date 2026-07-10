"""Client API for CoMemBus."""

from __future__ import annotations

from dataclasses import dataclass
import socket
import threading
from typing import Any, Dict, Optional

from .metrics.recorder import MetricsRecorder
from .object_store.shm_store import SharedMemoryObjectStore
from .protocol import Message, ProtocolError
from .reliability.delivery import MessageNotFoundError, QueueFullError
from .transport.uds import connect_unix_socket, recv_frame, send_frame


class AgentBusClientError(Exception):
    """Raised when the server returns an error response."""


@dataclass
class AgentBusClient:
    """Simple UDS client for the CoMemBus server."""

    socket_path: str
    timeout: float = 5.0
    metrics_recorder: Optional[MetricsRecorder] = None

    def __post_init__(self) -> None:
        self._socket = connect_unix_socket(self.socket_path, self.timeout)
        self._lock = threading.Lock()
        self._closed = False
        self.object_store = SharedMemoryObjectStore(self.metrics_recorder)

    def register(self, agent_id: str) -> Dict[str, Any]:
        return self._request(Message(type="register", payload={"agent_id": agent_id}))

    def publish(
        self,
        topic: str,
        payload: Dict[str, Any],
        message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        message = Message(type="publish", topic=topic, payload=payload)
        if message_id is not None:
            message = Message(
                type=message.type,
                topic=message.topic,
                payload=message.payload,
                message_id=message_id,
                created_at=message.created_at,
            )
        return self._request(message)

    def poll(self, topic: str) -> Optional[Dict[str, Any]]:
        data = self._request(
            Message(type="poll", topic=topic, payload={"auto_ack": True})
        )
        return data if data is None else dict(data)

    def poll_reliable(
        self,
        topic: str,
        consumer_agent: str = "",
        visibility_timeout: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "auto_ack": False,
            "consumer_agent": consumer_agent,
        }
        if visibility_timeout is not None:
            payload["visibility_timeout"] = float(visibility_timeout)
        data = self._request(Message(type="poll", topic=topic, payload=payload))
        return data if data is None else dict(data)

    def ack(self, message_id: str, result: Any = None) -> Dict[str, Any]:
        return self._request(
            Message(
                type="ack",
                payload={"message_id": message_id, "result": result},
            )
        )

    def nack(self, message_id: str) -> Dict[str, Any]:
        return self._request(
            Message(type="nack", payload={"message_id": message_id})
        )

    def renew_visibility(
        self,
        message_id: str,
        visibility_timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"message_id": message_id}
        if visibility_timeout is not None:
            payload["visibility_timeout"] = float(visibility_timeout)
        return self._request(Message(type="renew_visibility", payload=payload))

    def ping(self) -> bool:
        data = self._request(Message(type="ping"))
        return data == "pong"

    def shutdown(self) -> bool:
        data = self._request(Message(type="shutdown"))
        return data == "shutting down"

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.object_store.close()
        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._socket.close()

    def _request(self, message: Message) -> Any:
        if self._closed:
            raise AgentBusClientError("client is closed")
        try:
            with self._lock:
                send_frame(self._socket, message.to_dict(), self.metrics_recorder)
                response = recv_frame(self._socket, self.metrics_recorder)
        except OSError as exc:
            raise AgentBusClientError("socket communication failed") from exc
        except ProtocolError:
            raise

        if not isinstance(response, dict):
            raise AgentBusClientError("server returned a non-object response")
        if response.get("ok") is True:
            return response.get("data")
        error_message = str(response.get("error", "unknown server error"))
        error_type = response.get("error_type")
        if error_type == "QueueFullError":
            raise QueueFullError(error_message)
        if error_type == "MessageNotFoundError":
            raise MessageNotFoundError(error_message)
        raise AgentBusClientError(error_message)
