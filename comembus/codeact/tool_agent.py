"""Minimal CodeAct tool agent using the restricted sandbox."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from comembus.state.task_state import TaskState

from .sandbox import run_code_sandbox


@dataclass
class CodeActToolAgent:
    """Run a small validated code snippet over structured task facts."""

    default_timeout_sec: float = 2.0

    def run(
        self,
        task_state: TaskState | None = None,
        facts: Mapping[str, Any] | None = None,
        code: str | None = None,
        timeout_sec: float | None = None,
    ) -> dict[str, object]:
        selected_facts = dict(facts or (task_state.facts if task_state is not None else {}))
        code_text = code if isinstance(code, str) and code.strip() else self.build_mock_code()
        sandbox_result = run_code_sandbox(
            code=code_text,
            inputs=selected_facts,
            timeout_sec=self.default_timeout_sec if timeout_sec is None else timeout_sec,
        )
        result_payload = sandbox_result.get("result")
        root_cause = ""
        if isinstance(result_payload, dict):
            root_cause = str(result_payload.get("root_cause", ""))
        return {
            "ok": bool(sandbox_result["ok"]),
            "code": code_text,
            "facts": selected_facts,
            "sandbox": sandbox_result,
            "root_cause": root_cause,
            "used_mock_code": code_text == self.build_mock_code(),
        }

    def build_mock_code(self) -> str:
        return "\n".join(
            [
                "log_error = ''",
                "config_port = ''",
                "if 'log_error' in inputs:",
                "    log_error = str(inputs['log_error'])",
                "if 'config_port' in inputs:",
                "    config_port = str(inputs['config_port'])",
                "if 'database timeout' in log_error and 'wrong' in config_port:",
                "    result = {'root_cause': 'wrong database port caused database timeout'}",
                "else:",
                "    result = {'root_cause': 'root cause unresolved'}",
            ]
        )
