"""End-to-end tests for the CoMemBus MVP."""

from __future__ import annotations

import os
import tempfile
import time
import unittest

from comembus.client import AgentBusClient
from comembus.object_store.shm_store import ObjectStoreError
from comembus.protocol import ObjectRef
from comembus.server import AgentBusServer


class EndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="comembus-test-")
        self.socket_path = os.path.join(self.tempdir.name, "comembus.sock")
        self.server = AgentBusServer(self.socket_path)
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.tempdir.cleanup()

    def test_pubsub_shared_memory_flow(self) -> None:
        producer = AgentBusClient(self.socket_path)
        consumer = AgentBusClient(self.socket_path)
        ref = None

        try:
            self.assertEqual(producer.register("producer")["agent_id"], "producer")
            self.assertEqual(consumer.register("consumer")["agent_id"], "consumer")
            self.assertTrue(producer.ping())

            payload = os.urandom(1024 * 1024)
            ref = producer.object_store.put_bytes(payload)
            producer.publish("logs", {"object_ref": ref.to_dict()})

            message = consumer.poll("logs")
            self.assertIsNotNone(message)
            received_ref = ObjectRef.from_dict(message["object_ref"])
            restored = consumer.object_store.get_bytes(received_ref)

            self.assertEqual(restored, payload)
            self.assertEqual(restored[:64], payload[:64])
            self.assertIsNone(consumer.poll("logs"))
        finally:
            if ref is not None:
                try:
                    consumer.object_store.unlink(ref)
                except ObjectStoreError:
                    pass
            producer.close()
            consumer.close()

    def test_shutdown_command_stops_server(self) -> None:
        admin = AgentBusClient(self.socket_path)
        try:
            self.assertTrue(admin.shutdown())
            deadline = time.time() + 2.0
            while os.path.exists(self.socket_path) and time.time() < deadline:
                time.sleep(0.05)
            self.assertFalse(os.path.exists(self.socket_path))
        finally:
            admin.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

