#!/usr/bin/env python3
"""Run the optional LLM-backed review agent demo."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comembus.llm.agent import LLMReviewAgent
from comembus.llm.local_http_client import resolve_model_name
from comembus.llm.openai_compatible_client import resolve_model as resolve_remote_model
from comembus.memory.blackboard import SharedBlackboard
from comembus.memory.unit import MemoryUnit
from comembus.state.task_state import TaskState
from examples.incident_diagnosis_mock.scenarios import IncidentScenario, default_scenarios


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        default="mock",
        choices=["mock", "local_http", "openai_compatible"],
        help="Optional LLM provider. Defaults to offline mock.",
    )
    parser.add_argument(
        "--endpoint",
        default="",
        help="Optional local OpenAI-compatible endpoint for provider=local_http.",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Optional local model name for provider=local_http.",
    )
    parser.add_argument(
        "--db-path",
        default="results/llm_agent_demo.sqlite",
        help="SQLite path for temporary memory reuse data.",
    )
    parser.add_argument(
        "--api-key-env",
        default="COMEMBUS_LLM_API_KEY",
        help="Environment variable name for provider=openai_compatible.",
    )
    return parser.parse_args(argv)


def build_demo_task_state(scenario: IncidentScenario) -> TaskState:
    return TaskState(
        task_id=f"llm-demo-task-{scenario.task_index}",
        version=3,
        goal=f"Diagnose a {scenario.family} incident with compressed structured context",
        phase="review_ready",
        completed_steps=["planning", "log_analysis", "config_check"],
        pending_steps=["review"],
        facts={
            "scenario_family": scenario.family,
            "log_error": scenario.log_pattern,
            "log_summary": f"log analysis found: {scenario.log_pattern}",
            "config_issue": scenario.config_issue,
            "config_summary": f"configuration review found: {scenario.config_issue}",
            "expected_root_cause": scenario.expected_root_cause,
            "related_memory_query": scenario.related_memory_query,
        },
        errors=[],
        artifacts={},
    )


def seed_demo_memories(
    board: SharedBlackboard,
    scenario: IncidentScenario,
    task_id: str,
) -> List[MemoryUnit]:
    board.write_memory(
        task_id=f"{task_id}-prior-1",
        source_agent="review-agent",
        task_topic=scenario.task_topic,
        memory_type="summary",
        summary=f"{scenario.family} prior diagnosis",
        content=f"root_cause={scenario.expected_root_cause}",
        tags=list(scenario.tags) + [scenario.family],
        confidence=0.95,
        metadata={"source": "llm_demo"},
    )
    board.write_memory(
        task_id=f"{task_id}-prior-2",
        source_agent="memory-agent",
        task_topic=scenario.task_topic,
        memory_type="strategy",
        summary=f"reuse {scenario.family} compressed evidence before raw log replay",
        content=(
            "prefer structured facts, state patch outputs, and short evidence before "
            "reading large raw logs again"
        ),
        tags=list(scenario.tags) + [scenario.family, "strategy"],
        confidence=0.92,
        metadata={"source": "llm_demo"},
    )
    hits = board.search(
        scenario.related_memory_query,
        tags=list(scenario.tags) + [scenario.family],
        top_k=3,
    )
    return [hit.memory for hit in hits]


def run_llm_agent_demo(
    provider: str = "mock",
    endpoint: str | None = None,
    model: str | None = None,
    api_key_env: str = "COMEMBUS_LLM_API_KEY",
    db_path: str = "results/llm_agent_demo.sqlite",
) -> Dict[str, object]:
    results_path = Path(db_path)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    if results_path.exists():
        results_path.unlink()

    scenario = default_scenarios()[0]
    task_state = build_demo_task_state(scenario)
    board = SharedBlackboard(str(results_path))
    try:
        memories = seed_demo_memories(board, scenario, task_state.task_id)
        evidence = [
            task_state.facts.get("log_summary", ""),
            task_state.facts.get("config_summary", ""),
        ]
        resolved_model = "mock"
        if provider == "local_http":
            resolved_model = resolve_model_name(model)
        elif provider == "openai_compatible":
            resolved_model = resolve_remote_model(model)
        agent = LLMReviewAgent.from_provider(
            provider=provider,
            endpoint=endpoint or None,
            model=model or None,
            api_key_env=api_key_env,
        )
        result = agent.review(task_state=task_state, memories=memories, evidence=evidence)
        result["task_id"] = task_state.task_id
        result["model"] = str(result.get("model") or resolved_model)
        return result
    finally:
        board.close()


def main() -> int:
    args = parse_args()
    result = run_llm_agent_demo(
        provider=args.provider,
        endpoint=args.endpoint or None,
        model=args.model or None,
        api_key_env=args.api_key_env,
        db_path=args.db_path,
    )
    print(f"provider={result['provider']}")
    print(f"model={result['model']}")
    print(f"used_fallback={str(bool(result['used_fallback'])).lower()}")
    if result.get("total_tokens") is not None:
        print(f"total_tokens={result['total_tokens']}")
    print(f"root_cause={result['root_cause']}")
    print(f"report={result['report']}")
    print("OK: llm agent demo completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
