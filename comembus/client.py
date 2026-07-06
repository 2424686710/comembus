"""Client API for CoMemBus."""

from __future__ import annotations

from dataclasses import dataclass
import socket
import threading
from typing import Any, Dict, Optional

from .object_store.shm_store import SharedMemoryObjectStore
from .protocol import Message, ProtocolError
from .transport.uds import connect_unix_socket, recv_frame, send_frame


class AgentBusClientError(Exception):
    """Raised when the server returns an error response."""


@dataclass
class AgentBusClient:
    """Simple UDS client for the CoMemBus server."""

    socket_path: str
    timeout: float = 5.0

    def __post_init__(self) -> None:
        self._socket = connect_unix_socket(self.socket_path, self.timeout)
        self._lock = threading.Lock()
        self._closed = False
        self.object_store = SharedMemoryObjectStore()

    def register(self, agent_id: str) -> Dict[str, Any]:
        return self._request(Message(type="register", payload={"agent_id": agent_id}))

    def publish(self, topic: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request(Message(type="publish", topic=topic, payload=payload))

    def poll(self, topic: str) -> Optional[Dict[str, Any]]:
        data = self._request(Message(type="poll", topic=topic))
        return data if data is None else dict(data)

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
                send_frame(self._socket, message.to_dict())
                response = recv_frame(self._socket)
        except OSError as exc:
            raise AgentBusClientError("socket communication failed") from exc
        except ProtocolError:
            raise

        if not isinstance(response, dict):
            raise AgentBusClientError("server returned a non-object response")
        if response.get("ok") is True:
            return response.get("data")
        raise AgentBusClientError(str(response.get("error", "unknown server error")))

