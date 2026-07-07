"""Capability registry for structured collaboration experiments."""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Optional

from comembus.collab.protocol import AgentCapability


def default_capabilities() -> List[AgentCapability]:
    """Return the default mock agent capability set used by CoMemBus demos."""

    return [
        AgentCapability(
            agent_id="planner-agent",
            role="planner",
            actions=["create_plan", "dispatch_task", "review_capabilities"],
            input_types=["task_topic", "memory_refs"],
            output_types=["structured_action"],
            description="Creates plans and dispatches work to specialized agents.",
        ),
        AgentCapability(
            agent_id="log-agent",
            role="log_analysis",
            actions=["analyze_log", "extract_log_facts", "scan_error_pattern"],
            input_types=["object_ref", "task_state"],
            output_types=["state_patch", "embedding_state"],
            description="Reads shared-memory logs and extracts structured log facts.",
        ),
        AgentCapability(
            agent_id="config-agent",
            role="config_analysis",
            actions=["check_config", "extract_config_facts", "validate_permission"],
            input_types=["config_text", "task_state"],
            output_types=["state_patch"],
            description="Inspects configuration content and emits config facts.",
        ),
        AgentCapability(
            agent_id="review-agent",
            role="review",
            actions=["summarize_result", "verify_root_cause", "generate_report"],
            input_types=["task_state", "memory_refs", "embedding_ref"],
            output_types=["root_cause_report"],
            description="Builds the final structured review report.",
        ),
        AgentCapability(
            agent_id="memory-agent",
            role="memory",
            actions=["write_memory", "search_memory", "reuse_strategy"],
            input_types=["memory_query", "memory_payload"],
            output_types=["memory_refs"],
            description="Searches and persists shared memories for later reuse.",
        ),
    ]


class CapabilityRegistry:
    """In-memory registry of available agent capabilities."""

    def __init__(self, capabilities: Iterable[AgentCapability] | None = None) -> None:
        self._capabilities: Dict[str, AgentCapability] = {}
        for capability in capabilities or []:
            self.register(capability)

    def register(self, capability: AgentCapability) -> None:
        if not isinstance(capability, AgentCapability):
            raise TypeError("capability must be an AgentCapability")
        self._capabilities[capability.agent_id] = AgentCapability.from_dict(capability.to_dict())

    def unregister(self, agent_id: str) -> None:
        if not isinstance(agent_id, str):
            raise TypeError("agent_id must be a string")
        self._capabilities.pop(agent_id, None)

    def get(self, agent_id: str) -> AgentCapability | None:
        if not isinstance(agent_id, str):
            raise TypeError("agent_id must be a string")
        capability = self._capabilities.get(agent_id)
        if capability is None:
            return None
        return AgentCapability.from_dict(capability.to_dict())

    def list_all(self) -> List[AgentCapability]:
        return [
            AgentCapability.from_dict(capability.to_dict())
            for capability in self._capabilities.values()
        ]

    def discover_by_action(self, action_type: str) -> List[AgentCapability]:
        if not isinstance(action_type, str):
            raise TypeError("action_type must be a string")
        return [
            AgentCapability.from_dict(capability.to_dict())
            for capability in self._capabilities.values()
            if action_type in capability.actions
        ]

    def discover_by_role(self, role: str) -> List[AgentCapability]:
        if not isinstance(role, str):
            raise TypeError("role must be a string")
        return [
            AgentCapability.from_dict(capability.to_dict())
            for capability in self._capabilities.values()
            if capability.role == role
        ]

    def select_agent(
        self,
        action_type: str,
        preferred_role: str | None = None,
    ) -> AgentCapability | None:
        candidates = self.discover_by_action(action_type)
        if preferred_role is not None:
            for capability in candidates:
                if capability.role == preferred_role:
                    return capability
        return candidates[0] if candidates else None

    def to_dict(self) -> Dict[str, object]:
        return {
            "capabilities": [capability.to_dict() for capability in self.list_all()],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "CapabilityRegistry":
        if not isinstance(data, Mapping):
            raise TypeError("capability registry payload must be a mapping")
        raw_capabilities = data.get("capabilities", [])
        if not isinstance(raw_capabilities, list):
            raise TypeError("capabilities must be a list")
        return cls(AgentCapability.from_dict(item) for item in raw_capabilities)
