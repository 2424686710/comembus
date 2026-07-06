#!/usr/bin/env python3
"""Smoke test for UDS control messages plus shared-memory payload exchange."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comembus.client import AgentBusClient
from comembus.protocol import ObjectRef
from comembus.server import AgentBusServer


def main() -> int:
    tempdir = tempfile.TemporaryDirectory(prefix="comembus-demo-")
    socket_path = os.path.join(tempdir.name, "comembus.sock")
    server = AgentBusServer(socket_path)
    producer = None
    consumer = None
    ref = None

    try:
        server.start()
        producer = AgentBusClient(socket_path)
        consumer = AgentBusClient(socket_path)

        producer.register("producer")
        consumer.register("consumer")

        expected = os.urandom(8 * 1024 * 1024)
        ref = producer.object_store.put_bytes(expected)
        producer.publish("logs", {"object_ref": ref.to_dict()})

        message = consumer.poll("logs")
        if message is None:
            raise RuntimeError("consumer did not receive a message")

        received_ref = ObjectRef.from_dict(message["object_ref"])
        actual = consumer.object_store.get_bytes(received_ref)
        if actual != expected:
            raise RuntimeError("shared-memory payload mismatch")

        consumer.object_store.unlink(received_ref)
        ref = None
        print("OK: shared-memory pubsub smoke test passed")
        return 0
    finally:
        if ref is not None and consumer is not None:
            try:
                consumer.object_store.unlink(ref)
            except Exception:
                pass
        if producer is not None:
            producer.close()
        if consumer is not None:
            consumer.close()
        server.stop()
        tempdir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())

