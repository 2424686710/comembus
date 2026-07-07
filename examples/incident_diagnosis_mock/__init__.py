"""Mock multi-agent incident diagnosis demo for CoMemBus."""

from .agents import (
    AdaptiveTransportPolicy,
    ConfigAgent,
    LogAgent,
    PlannerAgent,
    ReviewAgent,
    build_config_state_patch,
    build_initial_task_state,
    build_log_state_patch,
    build_mock_config_text,
    build_mock_log_blob,
    build_review_report_from_state,
    summarize_incident,
)
from .scenarios import (
    IncidentScenario,
    default_scenarios,
    load_scenarios,
    scenario_to_config_text,
    scenario_to_log_bytes,
)

__all__ = [
    "AdaptiveTransportPolicy",
    "ConfigAgent",
    "IncidentScenario",
    "LogAgent",
    "PlannerAgent",
    "ReviewAgent",
    "build_config_state_patch",
    "build_initial_task_state",
    "build_log_state_patch",
    "build_mock_config_text",
    "build_mock_log_blob",
    "build_review_report_from_state",
    "default_scenarios",
    "load_scenarios",
    "scenario_to_config_text",
    "scenario_to_log_bytes",
    "summarize_incident",
]
