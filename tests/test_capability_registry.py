"""Tests for capability discovery and handshake helpers."""

from __future__ import annotations

import unittest

from comembus.capability.handshake import accept_handshake, build_handshake_request
from comembus.capability.registry import CapabilityRegistry, default_capabilities
from comembus.collab.protocol import AgentCapability


class CapabilityRegistryTests(unittest.TestCase):
    def test_default_capabilities_cover_expected_roles_and_actions(self) -> None:
        registry = CapabilityRegistry(default_capabilities())

        self.assertEqual(len(registry.list_all()), 5)
        self.assertEqual(registry.select_agent("analyze_log").agent_id, "log-agent")
        self.assertEqual(
            registry.select_agent("summarize_result", preferred_role="review").agent_id,
            "review-agent",
        )
        self.assertEqual(
            [cap.agent_id for cap in registry.discover_by_role("memory")],
            ["memory-agent"],
        )

    def test_registry_round_trip_and_unregister(self) -> None:
        registry = CapabilityRegistry()
        capability = AgentCapability(
            agent_id="custom-agent",
            role="custom",
            actions=["do_custom"],
            input_types=["task"],
            output_types=["result"],
            description="custom agent",
        )
        registry.register(capability)

        restored = CapabilityRegistry.from_dict(registry.to_dict())

        self.assertEqual(restored.get("custom-agent").to_dict(), capability.to_dict())
        restored.unregister("custom-agent")
        self.assertIsNone(restored.get("custom-agent"))

    def test_handshake_registers_log_agent_capability(self) -> None:
        registry = CapabilityRegistry()
        capability = AgentCapability(
            agent_id="log-agent",
            role="log_analysis",
            actions=["analyze_log", "extract_log_facts"],
            input_types=[],
            output_types=[],
            description="log agent",
        )

        response = accept_handshake(build_handshake_request(capability), registry)

        self.assertTrue(response.accepted)
        discovered = registry.discover_by_action("analyze_log")
        self.assertEqual([cap.agent_id for cap in discovered], ["log-agent"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
