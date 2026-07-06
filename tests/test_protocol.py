"""Protocol tests for CoMemBus."""

from __future__ import annotations

from dataclasses import replace
import socket
import struct
import unittest

from comembus.protocol import (
    EmptyFrameError,
    FrameTooLargeError,
    InvalidJSONError,
    MAX_FRAME_SIZE,
    Message,
    ObjectRef,
    decode_frame_from_socket,
    encode_frame,
)
from comembus.transport.uds import send_frame


class ProtocolTests(unittest.TestCase):
    def test_object_ref_round_trip(self) -> None:
        ref = ObjectRef(
            object_id="obj-1",
            shm_name="comembus_obj-1",
            size=128,
            checksum="abc123",
            created_at=123.5,
        )
        self.assertEqual(ObjectRef.from_dict(ref.to_dict()), ref)

    def test_message_round_trip(self) -> None:
        message = Message(type="publish", topic="logs", payload={"answer": 42})
        self.assertEqual(Message.from_dict(message.to_dict()), message)

    def test_frame_round_trip_over_socketpair(self) -> None:
        left, right = socket.socketpair()
        try:
            payload = {"type": "ping", "topic": None, "payload": {"ok": True}}
            send_frame(left, payload)
            received = decode_frame_from_socket(right)
            self.assertEqual(received, payload)
        finally:
            left.close()
            right.close()

    def test_empty_frame_is_rejected(self) -> None:
        left, right = socket.socketpair()
        try:
            left.sendall(struct.pack(">I", 0))
            with self.assertRaises(EmptyFrameError):
                decode_frame_from_socket(right)
        finally:
            left.close()
            right.close()

    def test_oversized_frame_is_rejected(self) -> None:
        left, right = socket.socketpair()
        try:
            left.sendall(struct.pack(">I", MAX_FRAME_SIZE + 1))
            with self.assertRaises(FrameTooLargeError):
                decode_frame_from_socket(right)
        finally:
            left.close()
            right.close()

    def test_invalid_json_is_rejected(self) -> None:
        left, right = socket.socketpair()
        try:
            body = b"{not-json"
            left.sendall(struct.pack(">I", len(body)) + body)
            with self.assertRaises(InvalidJSONError):
                decode_frame_from_socket(right)
        finally:
            left.close()
            right.close()

    def test_encode_frame_rejects_too_large_json(self) -> None:
        message = Message(type="publish", topic="logs", payload={"blob": "x" * MAX_FRAME_SIZE})
        with self.assertRaises(FrameTooLargeError):
            encode_frame(message.to_dict())


if __name__ == "__main__":
    unittest.main(verbosity=2)

