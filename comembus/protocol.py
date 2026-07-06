"""Wire protocol primitives for CoMemBus."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import socket
import struct
from typing import Any, Dict, Mapping, Optional

FRAME_HEADER_SIZE = 4
MAX_FRAME_SIZE = 1024 * 1024


class ProtocolError(Exception):
    """Base protocol error."""


class ConnectionClosedError(ProtocolError):
    """Raised when the peer closes the socket unexpectedly."""


class FrameTooLargeError(ProtocolError):
    """Raised when a frame exceeds the configured size limit."""


class EmptyFrameError(ProtocolError):
    """Raised when a frame advertises an empty body."""


class InvalidJSONError(ProtocolError):
    """Raised when a frame body cannot be decoded as JSON."""


@dataclass(frozen=True)
class ObjectRef:
    """Metadata required to locate and validate a shared-memory object."""

    object_id: str
    shm_name: str
    size: int
    checksum: str
    created_at: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "object_id": self.object_id,
            "shm_name": self.shm_name,
            "size": self.size,
            "checksum": self.checksum,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ObjectRef":
        try:
            return cls(
                object_id=str(data["object_id"]),
                shm_name=str(data["shm_name"]),
                size=int(data["size"]),
                checksum=str(data["checksum"]),
                created_at=float(data["created_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError("invalid ObjectRef payload") from exc


@dataclass(frozen=True)
class Message:
    """Small control-plane message carried over UDS."""

    type: str
    topic: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "topic": self.topic,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Message":
        try:
            message_type = data["type"]
            topic = data.get("topic")
            payload = data.get("payload", {})
        except AttributeError as exc:
            raise ProtocolError("message must be a mapping") from exc

        if not isinstance(message_type, str) or not message_type:
            raise ProtocolError("message type must be a non-empty string")
        if topic is not None and not isinstance(topic, str):
            raise ProtocolError("message topic must be a string or null")
        if not isinstance(payload, dict):
            raise ProtocolError("message payload must be a JSON object")

        return cls(type=message_type, topic=topic, payload=payload)


def encode_json(data: Mapping[str, Any]) -> bytes:
    """Encode a message dictionary into UTF-8 JSON bytes."""

    try:
        body = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError("message is not JSON serializable") from exc
    if not body:
        raise EmptyFrameError("frame body must not be empty")
    return body


def decode_json(data: bytes) -> Dict[str, Any]:
    """Decode UTF-8 JSON bytes into a message dictionary."""

    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidJSONError("invalid JSON body") from exc
    if not isinstance(value, dict):
        raise ProtocolError("frame payload must decode to a JSON object")
    return value


def encode_frame(message_dict: Mapping[str, Any]) -> bytes:
    """Encode a JSON message as a 4-byte length-prefixed frame."""

    body = encode_json(message_dict)
    if len(body) > MAX_FRAME_SIZE:
        raise FrameTooLargeError(
            f"frame body exceeds limit: {len(body)} > {MAX_FRAME_SIZE}"
        )
    header = struct.pack(">I", len(body))
    return header + body


def decode_frame_from_socket(sock: socket.socket) -> Dict[str, Any]:
    """Read and decode one frame from a socket."""

    header = _recv_exact(sock, FRAME_HEADER_SIZE)
    frame_length = struct.unpack(">I", header)[0]
    if frame_length == 0:
        raise EmptyFrameError("received empty frame")
    if frame_length > MAX_FRAME_SIZE:
        raise FrameTooLargeError(
            f"frame body exceeds limit: {frame_length} > {MAX_FRAME_SIZE}"
        )
    body = _recv_exact(sock, frame_length)
    return decode_json(body)


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        try:
            chunk = sock.recv(size - len(chunks))
        except socket.timeout as exc:
            raise ConnectionClosedError("socket read timed out") from exc
        if not chunk:
            if not chunks:
                raise ConnectionClosedError("peer closed the connection")
            raise ConnectionClosedError("connection closed mid-frame")
        chunks.extend(chunk)
    return bytes(chunks)

