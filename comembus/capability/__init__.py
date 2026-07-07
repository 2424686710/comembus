"""Capability discovery helpers for CoMemBus."""

from .handshake import (
    DEFAULT_PROTOCOL_VERSION,
    HandshakeRequest,
    HandshakeResponse,
    accept_handshake,
    build_handshake_request,
)
from .registry import CapabilityRegistry, default_capabilities

__all__ = [
    "CapabilityRegistry",
    "DEFAULT_PROTOCOL_VERSION",
    "HandshakeRequest",
    "HandshakeResponse",
    "accept_handshake",
    "build_handshake_request",
    "default_capabilities",
]
