"""Reliable AgentBus delivery, deduplication, visibility, and backpressure tests."""

from __future__ import annotations

import os
import tempfile
import unittest

from comembus.client import AgentBusClient
from comembus.protocol import Message
from comembus.reliability.delivery import (
    MessageNotFoundError,
    QueueFullError,
    ReliableDeliveryManager,
)
from comembus.server import AgentBusServer


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class ReliableDeliveryManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        self.manager = ReliableDeliveryManager(
            max_queue_size=2,
            visibility_timeout=5.0,
            clock=self.clock,
        )

    def test_visibility_timeout_requeues_with_incremented_attempt(self) -> None:
        self.manager.publish("jobs", {"work": 1}, message_id="message-1")
        first = self.manager.poll("jobs", consumer_agent="consumer-a")
        self.assertIsNotNone(first)
        self.assertEqual(first.delivery_attempt, 1)
        self.assertIsNone(self.manager.poll("jobs", consumer_agent="consumer-b"))

        self.clock.advance(6.0)
        second = self.manager.poll("jobs", consumer_agent="consumer-b")
        self.assertIsNotNone(second)
        self.assertEqual(second.delivery_attempt, 2)
        self.manager.ack(second.message_id, {"ok": True})
        self.assertEqual(self.manager.get_stats()["message_requeued_count"], 1)

    def test_ack_result_is_reused_for_duplicate_publish(self) -> None:
        self.manager.publish("jobs", {"work": 1}, message_id="dedup-1")
        envelope = self.manager.poll("jobs")
        self.manager.ack(envelope.message_id, {"business_result": 7})
        duplicate = self.manager.publish(
            "jobs", {"work": 1}, message_id="dedup-1"
        )
        self.assertTrue(duplicate["duplicate_suppressed"])
        self.assertTrue(duplicate["processed"])
        self.assertEqual(duplicate["processed_result"], {"business_result": 7})
        self.assertIsNone(self.manager.poll("jobs"))

    def test_nack_and_renew_visibility(self) -> None:
        self.manager.publish("jobs", {"work": 1}, message_id="renew-1")
        first = self.manager.poll("jobs")
        original_deadline = first.visibility_deadline
        self.clock.advance(1.0)
        renewed = self.manager.renew_visibility("renew-1", visibility_timeout=10.0)
        self.assertGreater(renewed["visibility_deadline"], original_deadline)
        self.clock.advance(5.0)
        self.assertIsNone(self.manager.poll("jobs"))
        self.manager.nack("renew-1")
        redelivered = self.manager.poll("jobs")
        self.assertEqual(redelivered.delivery_attempt, 2)

    def test_queue_backpressure_and_unknown_ack_raise(self) -> None:
        self.manager.publish("jobs", {"work": 1}, message_id="full-1")
        self.manager.publish("jobs", {"work": 2}, message_id="full-2")
        with self.assertRaises(QueueFullError):
            self.manager.publish("jobs", {"work": 3}, message_id="full-3")
        with self.assertRaises(MessageNotFoundError):
            self.manager.ack("unknown-message")


class AgentBusCompatibilityTests(unittest.TestCase):
    def test_message_metadata_and_legacy_client_interface(self) -> None:
        message = Message(type="publish", topic="jobs", payload={"x": 1})
        restored = Message.from_dict(message.to_dict())
        self.assertEqual(restored.message_id, message.message_id)
        self.assertEqual(restored.delivery_attempt, 0)
        self.assertGreater(restored.created_at, 0)
        self.assertIsNone(restored.visibility_deadline)

        with tempfile.TemporaryDirectory(prefix="comembus-reliable-client-") as directory:
            path = os.path.join(directory, "bus.sock")
            server = AgentBusServer(path, max_queue_size=1)
            server.start()
            producer = AgentBusClient(path)
            consumer = AgentBusClient(path)
            try:
                producer.publish("legacy", {"value": 1})
                with self.assertRaises(QueueFullError):
                    producer.publish("legacy", {"value": 2})
                self.assertEqual(consumer.poll("legacy"), {"value": 1})
                producer.publish("legacy", {"value": 2})
                envelope = consumer.poll_reliable(
                    "legacy", consumer_agent="consumer"
                )
                self.assertEqual(envelope["payload"], {"value": 2})
                self.assertTrue(consumer.ack(envelope["message_id"])["acked"])
            finally:
                producer.close()
                consumer.close()
                server.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
