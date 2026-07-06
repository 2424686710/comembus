"""In-memory CoMemBus server for the stage-1 MVP."""

from __future__ import annotations

from collections import defaultdict
import threading
import time
from typing import Any, DefaultDict, Dict, List, Optional, Set

from .protocol import Message, ProtocolError
from .transport.uds import UnixDomainSocketServer


class AgentBusServer:
    """Small UDS-based message bus server."""

    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path
        self._lock = threading.Lock()
        self._registered_agents: Set[str] = set()
        self._topics: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._uds_server = UnixDomainSocketServer(socket_path, self._handle_request)
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
                data = self._publish(message.topic, message.payload)
            elif message.type == "poll":
                data = self._poll(message.topic)
            elif message.type == "ping":
                data = "pong"
            elif message.type == "shutdown":
                data = self._shutdown()
            else:
                raise ValueError(f"unsupported command: {message.type}")
            return {"ok": True, "data": data}
        except (ProtocolError, ValueError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}

    def _register(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = payload.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("agent_id must be a non-empty string")
        with self._lock:
            self._registered_agents.add(agent_id)
        return {"agent_id": agent_id}

    def _publish(self, topic: Optional[str], payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(topic, str) or not topic:
            raise ValueError("topic must be a non-empty string")
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        with self._lock:
            self._topics[topic].append(dict(payload))
            queue_depth = len(self._topics[topic])
        return {"topic": topic, "queued": queue_depth}

    def _poll(self, topic: Optional[str]) -> Optional[Dict[str, Any]]:
        if not isinstance(topic, str) or not topic:
            raise ValueError("topic must be a non-empty string")
        with self._lock:
            if not self._topics[topic]:
                return None
            return self._topics[topic].pop(0)

    def _shutdown(self) -> str:
        threading.Thread(target=self._delayed_stop, daemon=True).start()
        return "shutting down"

    def _delayed_stop(self) -> None:
        time.sleep(0.05)
        self.stop()

