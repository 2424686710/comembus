"""Adapter interfaces for optional LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class LLMMessage:
    role: str
    content: str


@dataclass(frozen=True)
class LLMResponse:
    content: str
    provider: str
    latency_ms: float
    used_fallback: bool


class BaseLLMClient(ABC):
    """Abstract LLM client interface."""

    @abstractmethod
    def generate(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.0,
    ) -> LLMResponse:
        raise NotImplementedError


def build_llm_client(provider: str, endpoint: str | None = None) -> BaseLLMClient:
    normalized = provider.strip().lower() if isinstance(provider, str) else "mock"
    if normalized == "local_http":
        from .local_http_client import LocalHTTPChatClient

        return LocalHTTPChatClient(endpoint=endpoint)

    from .mock_client import MockLLMClient

    return MockLLMClient()
