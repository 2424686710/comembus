#!/usr/bin/env python3
"""Run the optional minimal CodeAct sandbox demo."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Dict, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comembus.codeact.tool_agent import CodeActToolAgent
from comembus.memory.blackboard import SharedBlackboard
from comembus.state.task_state import TaskState


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        default="results/codeact_demo.sqlite",
        help="SQLite path for the demo blackboard.",
    )
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=2.0,
        help="Sandbox timeout in seconds.",
    )
    return parser.parse_args(argv)


def build_codeact_task_state() -> TaskState:
    return TaskState(
        task_id="codeact-demo-task",
        version=1,
        goal="Use a minimal sandbox to derive a safe structured incident summary",
        phase="codeact_ready",
        completed_steps=["planning", "fact_collection"],
        pending_steps=["codeact_review"],
        facts={
            "service_name": "checkout-api",
            "log_error": "database timeout",
            "config_port": "wrong database port",
            "evidence": "compressed log and config facts only",
        },
        errors=[],
        artifacts={},
    )


def run_codeact_demo(
    db_path: str = "results/codeact_demo.sqlite",
    timeout_sec: float = 2.0,
    code: str | None = None,
) -> Dict[str, object]:
    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    if db_file.exists():
        db_file.unlink()

    task_state = build_codeact_task_state()
    board = SharedBlackboard(str(db_file))
    agent = CodeActToolAgent(default_timeout_sec=timeout_sec)
    try:
        agent_result = agent.run(task_state=task_state, code=code, timeout_sec=timeout_sec)
        sandbox_result = agent_result["sandbox"]
        root_cause = str(agent_result.get("root_cause", ""))
        memory = board.write_memory(
            task_id=task_state.task_id,
            source_agent="codeact-tool-agent",
            task_topic="incident_diagnosis",
            memory_type="strategy",
            summary="codeact sandbox returned a structured root cause",
            content=f"root_cause={root_cause}",
            tags=["codeact", "strategy", "incident"],
            confidence=0.90 if bool(agent_result["ok"]) else 0.10,
            metadata={
                "sandbox_ok": bool(agent_result["ok"]),
                "timeout": bool(sandbox_result["timeout"]),
            },
        )
        return {
            "ok": bool(agent_result["ok"]),
            "task_id": task_state.task_id,
            "result": sandbox_result["result"],
            "sandbox": sandbox_result,
            "root_cause": root_cause,
            "memory_id": memory.memory_id,
            "used_mock_code": bool(agent_result["used_mock_code"]),
        }
    finally:
        board.close()


def main() -> int:
    args = parse_args()
    result = run_codeact_demo(
        db_path=args.db_path,
        timeout_sec=args.timeout_sec,
    )
    print(f"result={result['result']}")
    print(f"memory_id={result['memory_id']}")
    if not result["ok"]:
        print(f"error={result['sandbox']['error']}")
        return 1
    print("OK: codeact demo completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
