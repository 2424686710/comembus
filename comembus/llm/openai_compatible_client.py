"""Remote OpenAI-compatible chat completions client with fallback."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List

from .adapter import BaseLLMClient, LLMMessage, LLMResponse
from .mock_client import MockLLMClient

DEFAULT_API_KEY_ENV = "COMEMBUS_LLM_API_KEY"


class OpenAICompatibleChatClient(BaseLLMClient):
    """Call a remote OpenAI-compatible chat completions endpoint."""

    def __init__(
        self,
        endpoint: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        api_key_env: str = DEFAULT_API_KEY_ENV,
        timeout: float = 30.0,
        fallback_client: BaseLLMClient | None = None,
    ) -> None:
        self._endpoint = resolve_endpoint(endpoint)
        self._model = resolve_model(model)
        self._api_key_env = api_key_env
        self._api_key = resolve_api_key(api_key=api_key, api_key_env=api_key_env)
        self._timeout = float(timeout)
        self._fallback = fallback_client or MockLLMClient()

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @property
    def model(self) -> str:
        return self._model

    @property
    def api_key_env(self) -> str:
        return self._api_key_env

    def generate(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.0,
    ) -> LLMResponse:
        started = time.perf_counter()
        if not self._endpoint or not self._api_key:
            return self._fallback_response(messages, temperature, started)

        payload = {
            "model": self._model,
            "messages": [{"role": item.role, "content": item.content} for item in messages],
            "temperature": float(temperature),
            "stream": False,
        }
        request = urllib.request.Request(
            self._endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw_body = response.read().decode("utf-8")
            parsed = parse_chat_completion_response(raw_body)
        except (
            urllib.error.URLError,
            TimeoutError,
            ValueError,
            OSError,
            json.JSONDecodeError,
        ):
            return self._fallback_response(messages, temperature, started)

        latency_ms = (time.perf_counter() - started) * 1000.0
        return LLMResponse(
            content=parsed["content"],
            provider="openai_compatible",
            latency_ms=latency_ms,
            used_fallback=False,
            model=self._model,
            prompt_tokens=parsed.get("prompt_tokens"),
            completion_tokens=parsed.get("completion_tokens"),
            total_tokens=parsed.get("total_tokens"),
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
            model=self._model,
            prompt_tokens=fallback.prompt_tokens,
            completion_tokens=fallback.completion_tokens,
            total_tokens=fallback.total_tokens,
        )


def resolve_endpoint(endpoint: str | None = None) -> str:
    if isinstance(endpoint, str) and endpoint.strip():
        return normalize_chat_endpoint(endpoint.strip())
    env_endpoint = os.environ.get("COMEMBUS_LLM_ENDPOINT", "").strip()
    if not env_endpoint:
        return ""
    return normalize_chat_endpoint(env_endpoint)


def normalize_chat_endpoint(url: str) -> str:
    normalized = url.strip()
    if not normalized:
        return normalized
    if normalized.endswith("/chat/completions"):
        return normalized
    stripped = normalized.rstrip("/")
    if stripped.endswith("/chat/completions"):
        return stripped
    if stripped.endswith("/v1"):
        return f"{stripped}/chat/completions"
    return f"{stripped}/chat/completions"


def resolve_model(model: str | None = None) -> str:
    if isinstance(model, str) and model.strip():
        return model.strip()
    env_model = os.environ.get("COMEMBUS_LLM_MODEL", "").strip()
    if env_model:
        return env_model
    return "local-model"


def resolve_api_key(
    api_key: str | None = None,
    api_key_env: str = DEFAULT_API_KEY_ENV,
) -> str:
    if isinstance(api_key, str) and api_key.strip():
        return api_key.strip()
    return os.environ.get(api_key_env, "").strip()


def parse_chat_completion_response(raw_body: str) -> Dict[str, Any]:
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

    usage = payload.get("usage", {})
    prompt_tokens = _optional_int_from_mapping(usage, "prompt_tokens")
    completion_tokens = _optional_int_from_mapping(usage, "completion_tokens")
    total_tokens = _optional_int_from_mapping(usage, "total_tokens")
    return {
        "content": content,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _optional_int_from_mapping(data: object, key: str) -> int | None:
    if not isinstance(data, dict):
        return None
    value = data.get(key)
    if not isinstance(value, int):
        return None
    return value
