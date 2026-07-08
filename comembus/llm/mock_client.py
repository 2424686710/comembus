"""Deterministic offline mock LLM client."""

from __future__ import annotations

import time
from typing import List, Tuple

from .adapter import BaseLLMClient, LLMMessage, LLMResponse


def infer_root_cause_from_text(text: str) -> str:
    lowered = text.lower()
    if "database timeout" in lowered and "wrong database port" in lowered:
        return "wrong database port caused database timeout"
    if "permission denied" in lowered:
        return "credential file permission denied blocked database access"
    if "storage full" in lowered or "no space left" in lowered or "nospaceleftondevice" in lowered:
        return "database storage volume full caused write failures"
    if "database timeout" in lowered:
        return "database connectivity issue likely caused the timeout"
    return "root cause unresolved"


def build_mock_report(text: str) -> Tuple[str, str]:
    root_cause = infer_root_cause_from_text(text)
    if root_cause == "wrong database port caused database timeout":
        report = (
            "Structured facts show repeated database timeout symptoms together with a "
            "wrong database port setting. Fix the port first and reuse prior memory "
            "before reprocessing raw logs."
        )
    elif root_cause == "credential file permission denied blocked database access":
        report = (
            "Structured evidence points to permission denied on the credential path. "
            "Correct file ownership or mode, then rerun the access validation step."
        )
    elif root_cause == "database storage volume full caused write failures":
        report = (
            "Structured evidence shows the database volume is full. Free space or "
            "rotate retained data before retrying write-heavy recovery steps."
        )
    elif root_cause == "database connectivity issue likely caused the timeout":
        report = (
            "The compressed state still points to a database timeout, but the final "
            "configuration signal is incomplete. Validate connectivity and key config "
            "paths before expanding the investigation."
        )
    else:
        report = (
            "The available structured state is insufficient to produce a stronger "
            "incident conclusion. Collect another targeted evidence snapshot."
        )
    return root_cause, report


class MockLLMClient(BaseLLMClient):
    """Offline deterministic provider used by default."""

    def generate(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.0,
    ) -> LLMResponse:
        del temperature
        started = time.perf_counter()
        text = "\n".join(message.content for message in messages)
        root_cause, report = build_mock_report(text)
        content = f"root_cause: {root_cause}\nreport: {report}"
        latency_ms = (time.perf_counter() - started) * 1000.0
        return LLMResponse(
            content=content,
            provider="mock",
            latency_ms=latency_ms,
            used_fallback=False,
            model="mock",
        )
