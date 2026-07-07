"""Simple capability handshake helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Dict, List, Mapping

from comembus.collab.protocol import AgentCapability

from .registry import CapabilityRegistry

DEFAULT_PROTOCOL_VERSION = "1.0"


@dataclass
class HandshakeRequest:
    agent_id: str
    role: str
    capabilities: List[str] = field(default_factory=list)
    protocol_version: str = DEFAULT_PROTOCOL_VERSION
    created_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "capabilities": list(self.capabilities),
            "protocol_version": self.protocol_version,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HandshakeRequest":
        capabilities = data.get("capabilities", [])
        if not isinstance(capabilities, list) or not all(
            isinstance(item, str) for item in capabilities
        ):
            raise TypeError("capabilities must be a list of strings")
        protocol_version = data.get("protocol_version", DEFAULT_PROTOCOL_VERSION)
        if not isinstance(protocol_version, str):
            raise TypeError("protocol_version must be a string")
        created_at = data.get("created_at", 0.0)
        if not isinstance(created_at, (int, float)):
            raise TypeError("created_at must be a number")
        agent_id = data.get("agent_id")
        role = data.get("role")
        if not isinstance(agent_id, str):
            raise TypeError("agent_id must be a string")
        if not isinstance(role, str):
            raise TypeError("role must be a string")
        return cls(
            agent_id=agent_id,
            role=role,
            capabilities=list(capabilities),
            protocol_version=protocol_version,
            created_at=float(created_at),
        )


@dataclass
class HandshakeResponse:
    accepted: bool
    agent_id: str
    protocol_version: str
    registered_actions: List[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accepted": self.accepted,
            "agent_id": self.agent_id,
            "protocol_version": self.protocol_version,
            "registered_actions": list(self.registered_actions),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HandshakeResponse":
        registered_actions = data.get("registered_actions", [])
        if not isinstance(registered_actions, list) or not all(
            isinstance(item, str) for item in registered_actions
        ):
            raise TypeError("registered_actions must be a list of strings")
        accepted = data.get("accepted")
        agent_id = data.get("agent_id")
        protocol_version = data.get("protocol_version")
        reason = data.get("reason", "")
        if not isinstance(accepted, bool):
            raise TypeError("accepted must be a boolean")
        if not isinstance(agent_id, str):
            raise TypeError("agent_id must be a string")
        if not isinstance(protocol_version, str):
            raise TypeError("protocol_version must be a string")
        if not isinstance(reason, str):
            raise TypeError("reason must be a string")
        return cls(
            accepted=accepted,
            agent_id=agent_id,
            protocol_version=protocol_version,
            registered_actions=list(registered_actions),
            reason=reason,
        )


def build_handshake_request(capability: AgentCapability) -> HandshakeRequest:
    if not isinstance(capability, AgentCapability):
        raise TypeError("capability must be an AgentCapability")
    return HandshakeRequest(
        agent_id=capability.agent_id,
        role=capability.role,
        capabilities=list(capability.actions),
        protocol_version=DEFAULT_PROTOCOL_VERSION,
        created_at=time.time(),
    )


def accept_handshake(
    request: HandshakeRequest,
    registry: CapabilityRegistry,
) -> HandshakeResponse:
    if not isinstance(request, HandshakeRequest):
        raise TypeError("request must be a HandshakeRequest")
    if not isinstance(registry, CapabilityRegistry):
        raise TypeError("registry must be a CapabilityRegistry")
    if request.protocol_version != DEFAULT_PROTOCOL_VERSION:
        return HandshakeResponse(
            accepted=False,
            agent_id=request.agent_id,
            protocol_version=DEFAULT_PROTOCOL_VERSION,
            registered_actions=[],
            reason=(
                f"unsupported protocol version: {request.protocol_version}; "
                f"expected {DEFAULT_PROTOCOL_VERSION}"
            ),
        )
    if not request.capabilities:
        return HandshakeResponse(
            accepted=False,
            agent_id=request.agent_id,
            protocol_version=DEFAULT_PROTOCOL_VERSION,
            registered_actions=[],
            reason="no capabilities provided",
        )

    capability = AgentCapability(
        agent_id=request.agent_id,
        role=request.role,
        actions=list(request.capabilities),
        input_types=[],
        output_types=[],
        description="registered via capability handshake",
    )
    registry.register(capability)
    return HandshakeResponse(
        accepted=True,
        agent_id=request.agent_id,
        protocol_version=DEFAULT_PROTOCOL_VERSION,
        registered_actions=list(request.capabilities),
        reason="accepted",
    )
