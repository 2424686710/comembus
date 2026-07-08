#!/usr/bin/env python3
"""Run an optional multi-agent LLM smoke test."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Dict, List, Sequence, Set

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comembus.llm.adapter import BaseLLMClient, LLMMessage, build_llm_client
from comembus.llm.agent import LLMReviewAgent, judge_root_cause_semantic as _judge_root_cause_semantic
from comembus.memory.blackboard import SharedBlackboard
from comembus.state.patch import apply_patch
from examples.incident_diagnosis_mock.run_llm_agent_demo import (
    build_demo_task_state,
    save_json_result,
    seed_demo_memories,
)
from examples.incident_diagnosis_mock.scenarios import default_scenarios


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        default="mock",
        choices=["mock", "local_http", "openai_compatible"],
        help="Optional LLM provider. Defaults to offline mock.",
    )
    parser.add_argument("--endpoint", default="", help="Optional endpoint override.")
    parser.add_argument("--model", default="", help="Optional model override.")
    parser.add_argument(
        "--api-key-env",
        default="COMEMBUS_LLM_API_KEY",
        help="Environment variable name for openai_compatible API key.",
    )
    parser.add_argument(
        "--llm-agents",
        default="planner,review",
        help='Comma-separated LLM-enabled agents, for example "planner,review" or "all".',
    )
    parser.add_argument(
        "--db-path",
        default="results/llm_multiagent_smoke.sqlite",
        help="SQLite path for temporary memory reuse data.",
    )
    parser.add_argument(
        "--save-json",
        default="",
        help="Optional JSON output path for the structured multi-agent LLM smoke result.",
    )
    return parser.parse_args(argv)


def parse_llm_agents(spec: str) -> Set[str]:
    tokens = {token.strip().lower() for token in spec.split(",") if token.strip()}
    if not tokens:
        return {"planner", "review"}
    if "all" in tokens:
        return {"planner", "log", "config", "review"}
    return tokens


def judge_root_cause_semantic(
    predicted: str,
    expected: str,
    scenario_tags: list[str] | None = None,
) -> bool:
    return _judge_root_cause_semantic(predicted, expected, scenario_tags)


def run_llm_multiagent_smoke(
    provider: str = "mock",
    endpoint: str | None = None,
    model: str | None = None,
    api_key_env: str = "COMEMBUS_LLM_API_KEY",
    llm_agents: str = "planner,review",
    db_path: str = "results/llm_multiagent_smoke.sqlite",
    save_json: str | None = None,
) -> Dict[str, object]:
    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    if db_file.exists():
        db_file.unlink()
    scenario = default_scenarios()[0]
    task_state = build_demo_task_state(scenario)
    selected_agents = parse_llm_agents(llm_agents)
    llm_client = build_llm_client(
        provider=provider,
        endpoint=endpoint,
        model=model,
        api_key_env=api_key_env,
    )
    board = SharedBlackboard(str(db_file))
    llm_call_count = 0
    used_fallback_count = 0
    llm_providers: List[str] = []
    try:
        memories = seed_demo_memories(board, scenario, task_state.task_id)
        action_list = ["log_analysis", "config_check", "review"]

        planner_plan = ""
        if "planner" in selected_agents:
            llm_call_count += 1
            planner_response = _planner_generate_plan(llm_client, task_state.goal, action_list)
            planner_plan = planner_response["content"]
            llm_providers.append(str(planner_response["provider"]))
            if planner_response["used_fallback"]:
                used_fallback_count += 1

        log_explanation = ""
        if "log" in selected_agents:
            llm_call_count += 1
            log_response = _generate_explanation(
                llm_client,
                role_name="log-agent",
                facts=[
                    "database timeout visible in logs",
                    "connection pool exhaustion visible in logs",
                ],
            )
            log_explanation = log_response["content"]
            llm_providers.append(str(log_response["provider"]))
            if log_response["used_fallback"]:
                used_fallback_count += 1

        log_patch = _log_patch_like_update(task_state)
        after_log = apply_patch(task_state, log_patch)

        config_explanation = ""
        if "config" in selected_agents:
            llm_call_count += 1
            config_response = _generate_explanation(
                llm_client,
                role_name="config-agent",
                facts=["configuration points to wrong database port"],
            )
            config_explanation = config_response["content"]
            llm_providers.append(str(config_response["provider"]))
            if config_response["used_fallback"]:
                used_fallback_count += 1

        config_patch = _config_patch_like_update(after_log)
        final_state = apply_patch(after_log, config_patch)

        review_payload: Dict[str, object]
        if "review" in selected_agents:
            llm_call_count += 1
            review_agent = LLMReviewAgent(
                llm_client=llm_client,
            )
            review_payload = review_agent.review(
                task_state=final_state,
                memories=memories,
                evidence=[
                    final_state.facts.get("log_summary", ""),
                    final_state.facts.get("config_summary", ""),
                ],
            )
            llm_providers.append(str(review_payload["provider"]))
            if bool(review_payload["used_fallback"]):
                used_fallback_count += 1
        else:
            review_payload = {
                "root_cause": scenario.expected_root_cause,
                "report": "Deterministic structured review report.",
                "provider": "deterministic",
                "used_fallback": False,
                "model": "deterministic",
                "total_tokens": None,
            }

        root_cause_correct = judge_root_cause_semantic(
            predicted=str(review_payload["root_cause"]),
            expected=scenario.expected_root_cause,
            scenario_tags=list(scenario.tags),
        )
        result_payload = {
            "llm_agents": ",".join(sorted(selected_agents)),
            "llm_call_count": llm_call_count,
            "used_fallback_count": used_fallback_count,
            "root_cause_correct": root_cause_correct,
            "root_cause_judge": "semantic",
            "provider": provider,
            "model": str(review_payload.get("model") or model or provider),
            "total_tokens": review_payload.get("total_tokens"),
            "planner_action_list": action_list,
            "planner_plan": planner_plan,
            "log_explanation": log_explanation,
            "config_explanation": config_explanation,
            "root_cause": review_payload["root_cause"],
            "report": review_payload["report"],
            "llm_providers": llm_providers,
        }
        if isinstance(save_json, str) and save_json.strip():
            result_payload["saved_json"] = save_json_result(
                save_json,
                {
                    "llm_agents": result_payload["llm_agents"],
                    "llm_call_count": result_payload["llm_call_count"],
                    "used_fallback_count": result_payload["used_fallback_count"],
                    "root_cause": result_payload["root_cause"],
                    "report": result_payload["report"],
                    "root_cause_judge": result_payload["root_cause_judge"],
                    "root_cause_correct": bool(result_payload["root_cause_correct"]),
                    "provider": result_payload["provider"],
                    "model": result_payload["model"],
                    "total_tokens": result_payload.get("total_tokens"),
                },
            )
        return result_payload
    finally:
        board.close()


def _planner_generate_plan(
    llm_client: BaseLLMClient,
    goal: str,
    action_list: Sequence[str],
) -> Dict[str, object]:
    response = llm_client.generate(
        [
            LLMMessage(
                role="system",
                content="You are a planner. Return a short plan description only.",
            ),
            LLMMessage(
                role="user",
                content=(
                    f"goal={goal}\n"
                    f"actions={','.join(action_list)}\n"
                    "Summarize the plan in one short paragraph."
                ),
            ),
        ]
    )
    return {
        "content": response.content,
        "provider": response.provider,
        "used_fallback": response.used_fallback,
    }


def _generate_explanation(
    llm_client: BaseLLMClient,
    role_name: str,
    facts: Sequence[str],
) -> Dict[str, object]:
    response = llm_client.generate(
        [
            LLMMessage(
                role="system",
                content=f"You are {role_name}. Explain the current structured evidence briefly.",
            ),
            LLMMessage(
                role="user",
                content="\n".join(facts),
            ),
        ]
    )
    return {
        "content": response.content,
        "provider": response.provider,
        "used_fallback": response.used_fallback,
    }


def _log_patch_like_update(state):  # type: ignore[no-untyped-def]
    from comembus.state.patch import StatePatch

    return StatePatch(
        task_id=state.task_id,
        expected_version=state.version,
        set_fields={"phase": "log_analysis_complete", "pending_steps": ["config_check", "review"]},
        append_fields={"completed_steps": ["log_analysis"]},
        merge_dict_fields={
            "facts": {
                "log_error": "database timeout",
                "log_signal": "connection pool exhausted",
                "log_summary": "database timeout and pool pressure were found in compressed log evidence",
            }
        },
    )


def _config_patch_like_update(state):  # type: ignore[no-untyped-def]
    from comembus.state.patch import StatePatch

    return StatePatch(
        task_id=state.task_id,
        expected_version=state.version,
        set_fields={"phase": "review_ready", "pending_steps": ["review"]},
        append_fields={"completed_steps": ["config_check"]},
        merge_dict_fields={
            "facts": {
                "config_issue": "wrong database port",
                "config_summary": "configuration review found the wrong database port",
                "config_service": "checkout-api",
            }
        },
    )


def main() -> int:
    args = parse_args()
    result = run_llm_multiagent_smoke(
        provider=args.provider,
        endpoint=args.endpoint or None,
        model=args.model or None,
        api_key_env=args.api_key_env,
        llm_agents=args.llm_agents,
        db_path=args.db_path,
        save_json=args.save_json or None,
    )
    print(f"llm_agents={result['llm_agents']}")
    print(f"llm_call_count={result['llm_call_count']}")
    print(f"used_fallback_count={result['used_fallback_count']}")
    print(f"root_cause_judge={result['root_cause_judge']}")
    print(f"root_cause_correct={str(bool(result['root_cause_correct'])).lower()}")
    if result.get("saved_json"):
        print(f"saved_json={result['saved_json']}")
    print("OK: llm multi-agent smoke completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
