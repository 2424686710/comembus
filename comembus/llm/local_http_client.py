"""Local HTTP client for OpenAI-compatible chat completions endpoints."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import List

from .adapter import BaseLLMClient, LLMMessage, LLMResponse
from .mock_client import MockLLMClient


class LocalHTTPChatClient(BaseLLMClient):
    """Call a local OpenAI-compatible chat completions endpoint."""

    def __init__(
        self,
        endpoint: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._endpoint = endpoint or os.environ.get("COMEMBUS_LLM_ENDPOINT", "")
        self._model = resolve_model_name(model)
        self._timeout_seconds = float(timeout_seconds)
        self._fallback = MockLLMClient()

    def generate(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.0,
    ) -> LLMResponse:
        started = time.perf_counter()
        if not self._endpoint:
            return self._fallback_response(messages, temperature, started)

        payload = {
            "model": self._model,
            "messages": [{"role": item.role, "content": item.content} for item in messages],
            "temperature": float(temperature),
        }
        request = urllib.request.Request(
            self._endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
            content = _extract_content(raw_body)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError):
            return self._fallback_response(messages, temperature, started)

        latency_ms = (time.perf_counter() - started) * 1000.0
        return LLMResponse(
            content=content,
            provider="local_http",
            latency_ms=latency_ms,
            used_fallback=False,
        )

    def _fallback_response(
        self,
        messages: List[LLMMessage],
        temperature: float,
        started: float,
    ) -> LLMResponse:
        fallback = self._fallback.generate(messages, temperature=temperature)
        latency_ms = (time.perf_counter() - started) * 1000.0
        return LLMResponse(
            content=fallback.content,
            provider=fallback.provider,
            latency_ms=latency_ms,
            used_fallback=True,
        )

    @property
    def model(self) -> str:
        return self._model


def resolve_model_name(model: str | None = None) -> str:
    if isinstance(model, str) and model.strip():
        return model.strip()
    env_model = os.environ.get("COMEMBUS_LLM_MODEL", "").strip()
    if env_model:
        return env_model
    return "local-model"


def _extract_content(raw_body: str) -> str:
    payload = json.loads(raw_body)
    if not isinstance(payload, dict):
        raise ValueError("response payload must be an object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("response choices missing")
    first = choices[0]
    if not isinstance(first, dict):
        raise ValueError("response choice must be an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise ValueError("response message missing")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("response content missing")
    return content
