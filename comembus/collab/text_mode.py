"""Pure text collaboration baseline."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import time
import uuid
from typing import Dict, List

from .metrics import CollaborationMetrics, estimate_tokens, json_size_bytes
from .protocol import TextMessage


@dataclass
class TextCollaborationRunner:
    """Simulate collaboration by repeatedly sending long text context."""

    task_index: int
    task_topic: str
    text_context_bytes: int = 65536
    baseline_steps: int = 5

    def run(self) -> CollaborationMetrics:
        started = time.perf_counter()
        previous_sections: List[str] = []
        messages: List[TextMessage] = []

        planner_text = self._build_text_message(
            stage="planner_to_log",
            goal="Diagnose a database connection timeout incident",
            current_full_context="No prior structured context is available.",
            log_excerpt=(
                "Simulated log summary: database timeout, connection pool exhaustion, "
                "request failures, and retry confusion."
            ),
            previous_facts="None yet.",
            next_instruction="Analyze the logs and produce a natural language summary.",
            expected_output=(
                "Return a prose diagnosis with full context, supporting evidence, "
                "and a recommended next step."
            ),
            previous_sections=previous_sections,
        )
        messages.append(
            self._make_message("planner-agent", "log-agent", self.task_topic, planner_text)
        )
        previous_sections.append(planner_text)

        log_text = self._build_text_message(
            stage="log_to_config",
            goal="Diagnose a database connection timeout incident",
            current_full_context="The planner asked for a full natural language handoff.",
            log_excerpt=(
                "Observed repeated DatabaseTimeout errors and ConnectionPoolExhausted "
                "warnings in checkout-api logs."
            ),
            previous_facts=(
                "Fact: database timeout is reproducible. "
                "Fact: connection pool exhaustion is visible in logs."
            ),
            next_instruction="Check configuration and compare it with the log findings.",
            expected_output="Return a full prose explanation plus any configuration suspicion.",
            previous_sections=previous_sections,
        )
        messages.append(
            self._make_message("log-agent", "config-agent", self.task_topic, log_text)
        )
        previous_sections.append(log_text)

        config_text = self._build_text_message(
            stage="config_to_review",
            goal="Diagnose a database connection timeout incident",
            current_full_context=(
                "The current conversation already includes planner context and the "
                "log agent's full prose handoff."
            ),
            log_excerpt="Relevant log context repeated for completeness: database timeout plus pool pressure.",
            previous_facts=(
                "Fact: database timeout remains the top log symptom. "
                "Fact: configuration review found a wrong database port."
            ),
            next_instruction="Review the entire incident narrative and determine the root cause.",
            expected_output="Return a final report with root cause and recommended action.",
            previous_sections=previous_sections,
        )
        messages.append(
            self._make_message("config-agent", "review-agent", self.task_topic, config_text)
        )
        previous_sections.append(config_text)

        final_root_cause = "wrong database port caused database timeout"
        review_text = self._build_text_message(
            stage="review_to_planner",
            goal="Diagnose a database connection timeout incident",
            current_full_context="The reviewer has received the complete textual history.",
            log_excerpt="Repeated database timeout and connection pool pressure evidence.",
            previous_facts=(
                "Fact: the configuration used the wrong database port. "
                f"Final root cause: {final_root_cause}."
            ),
            next_instruction="Close the incident and document the remediation plan.",
            expected_output=(
                "Return a final prose report with root cause, confidence, and next steps."
            ),
            previous_sections=previous_sections,
        )
        messages.append(
            self._make_message("review-agent", "planner-agent", self.task_topic, review_text)
        )

        # Simulate heavier baseline parsing of large textual context.
        processing_rounds = max(8, self.text_context_bytes // 4096)
        for message in messages:
            digest = message.text.encode("utf-8")
            for _ in range(processing_rounds):
                digest = hashlib.sha256(digest).digest()

        text_chars = sum(len(message.text) for message in messages)
        approx_tokens = sum(estimate_tokens(message.text) for message in messages)
        protocol_bytes = sum(json_size_bytes(message.to_dict()) for message in messages)
        measured_latency_ms = (time.perf_counter() - started) * 1000.0
        total_latency_ms = measured_latency_ms + (self.baseline_steps * 18.0)
        root_cause_correct = final_root_cause in review_text.lower()

        return CollaborationMetrics(
            mode="text_mode",
            task_index=self.task_index,
            task_topic=self.task_topic,
            message_count=len(messages),
            text_chars=text_chars,
            approx_tokens=approx_tokens,
            protocol_bytes=protocol_bytes,
            object_ref_count=0,
            state_patch_count=0,
            memory_ref_count=0,
            non_text_state_bytes=0,
            shared_object_bytes=0,
            memory_hit=False,
            reused_memory_id="",
            baseline_steps=self.baseline_steps,
            actual_steps=self.baseline_steps,
            saved_steps=0,
            total_latency_ms=total_latency_ms,
            root_cause_correct=root_cause_correct,
        )

    def _build_text_message(
        self,
        stage: str,
        goal: str,
        current_full_context: str,
        log_excerpt: str,
        previous_facts: str,
        next_instruction: str,
        expected_output: str,
        previous_sections: List[str],
    ) -> str:
        sections = [
            f"stage={stage}",
            f"task_topic={self.task_topic}",
            f"task_goal={goal}",
            f"current_full_context={current_full_context}",
            f"log_excerpt={log_excerpt}",
            f"previous_facts={previous_facts}",
            f"next_instruction={next_instruction}",
            f"expected_output_format={expected_output}",
        ]
        if previous_sections:
            sections.append("previous_messages=" + "\n---\n".join(previous_sections[-2:]))

        base_text = "\n".join(sections)
        if len(base_text.encode("utf-8")) >= self.text_context_bytes:
            return base_text[: self.text_context_bytes]

        repeated = []
        while len(("\n".join(sections + repeated)).encode("utf-8")) < self.text_context_bytes:
            repeated.append(
                "redundant_context=For compatibility with pure text collaboration, "
                "repeat the full task goal, prior narrative, evidence recap, and expected format."
            )
        return "\n".join(sections + repeated)

    def _make_message(
        self,
        source_agent: str,
        target_agent: str,
        task_id: str,
        text: str,
    ) -> TextMessage:
        return TextMessage(
            message_id=uuid.uuid4().hex,
            task_id=task_id,
            source_agent=source_agent,
            target_agent=target_agent,
            text=text,
            created_at=time.time(),
        )
