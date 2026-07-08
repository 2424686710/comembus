"""Optional LLM-backed review agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence

from comembus.memory.unit import MemoryUnit
from comembus.state.task_state import TaskState

from .adapter import BaseLLMClient, LLMMessage, build_llm_client
from .mock_client import infer_root_cause_from_text


@dataclass
class LLMReviewAgent:
    """Generate a natural-language review from compressed structured state."""

    llm_client: BaseLLMClient
    max_memories: int = 3
    max_evidence_items: int = 3

    @classmethod
    def from_provider(
        cls,
        provider: str = "mock",
        endpoint: str | None = None,
        model: str | None = None,
        api_key_env: str = "COMEMBUS_LLM_API_KEY",
    ) -> "LLMReviewAgent":
        return cls(
            llm_client=build_llm_client(
                provider=provider,
                endpoint=endpoint,
                model=model,
                api_key_env=api_key_env,
            )
        )

    def review(
        self,
        task_state: TaskState,
        memories: Sequence[MemoryUnit] | None = None,
        evidence: Sequence[str] | None = None,
    ) -> Dict[str, object]:
        prompt_messages = self._prompt_messages(task_state, memories or [], evidence or [])
        response = self.llm_client.generate(prompt_messages, temperature=0.0)
        root_cause, report = _parse_llm_content(response.content)
        if not root_cause:
            root_cause = self._fallback_root_cause(task_state, memories or [])
        if not report:
            report = (
                "The LLM response did not return a complete report, so the structured "
                "facts should be reviewed directly."
            )
        return {
            "root_cause": root_cause,
            "report": report,
            "provider": response.provider,
            "used_fallback": response.used_fallback,
            "model": response.model,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "total_tokens": response.total_tokens,
        }

    def _prompt_messages(
        self,
        task_state: TaskState,
        memories: Sequence[MemoryUnit],
        evidence: Sequence[str],
    ) -> List[LLMMessage]:
        facts_lines = [
            f"{key}={value}"
            for key, value in sorted(task_state.facts.items())
        ]
        selected_memories = list(memories[: self.max_memories])
        memory_lines = [
            f"{memory.memory_type}:{memory.summary}"
            for memory in selected_memories
        ]
        evidence_lines = [item for item in evidence[: self.max_evidence_items] if item]
        user_lines = [
            f"task_id={task_state.task_id}",
            f"version={task_state.version}",
            f"phase={task_state.phase}",
            f"goal={task_state.goal}",
            "facts:",
            *facts_lines,
            "memory_summaries:",
            *(memory_lines or ["none"]),
            "evidence:",
            *(evidence_lines or ["none"]),
            "Return exactly two lines:",
            "root_cause: <short root cause>",
            "report: <short incident report>",
            "Do not request raw logs. Use the compressed structured state only.",
        ]
        return [
            LLMMessage(
                role="system",
                content=(
                    "You are an optional review agent for CoMemBus. Summarize only from "
                    "structured state, short evidence, and memory summaries."
                ),
            ),
            LLMMessage(role="user", content="\n".join(user_lines)),
        ]

    def _fallback_root_cause(
        self,
        task_state: TaskState,
        memories: Sequence[MemoryUnit],
    ) -> str:
        text_parts = [task_state.goal]
        text_parts.extend(task_state.facts.values())
        text_parts.extend(memory.summary for memory in memories[: self.max_memories])
        return infer_root_cause_from_text("\n".join(text_parts))


def _parse_llm_content(content: str) -> tuple[str, str]:
    root_cause = ""
    report = ""
    for raw_line in content.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if lowered.startswith("root_cause:"):
            root_cause = line.split(":", 1)[1].strip()
        elif lowered.startswith("report:"):
            report = line.split(":", 1)[1].strip()
    if not root_cause and content.strip():
        first_line = content.strip().splitlines()[0].strip()
        if first_line:
            root_cause = first_line
    if not report and content.strip():
        report = content.strip()
    return root_cause, report
