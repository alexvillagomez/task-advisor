"""Versioned, deterministic outcome-composition rules."""

from __future__ import annotations

from typing import Any


RULES_VERSION = "ingest-outcome-v1.0.0"


def apply_outcome_rules(episode: dict[str, Any]) -> tuple[str, float, dict[str, bool]]:
    """Return tier, score, and the exact booleans used by the rule set.

    Precedence is intentional: a final failing verification is stronger than user
    silence, while a correction/retry prevents a passing check from being called
    verified success.
    """

    execution = episode.get("execution", {})
    followup = episode.get("followup", {})
    last_result = (execution.get("last") or {}).get("result")
    correction = followup.get("classification") in {"correction", "complaint"}
    retry = bool(followup.get("is_retry"))
    approval = followup.get("classification") == "approval"
    interrupted = followup.get("classification") == "interruption"
    has_agent_final = bool(episode.get("timing", {}).get("final_agent_output_at"))
    has_tool_errors = int(episode.get("tools", {}).get("error_count", 0)) > 0

    components = {
        "final_check_passed": last_result == "pass",
        "final_check_failed": last_result == "fail",
        "fail_to_pass": bool(execution.get("fail_to_pass")),
        "user_correction": correction,
        "user_retry": retry,
        "user_approval": approval,
        "interrupted": interrupted,
        "has_agent_final": has_agent_final,
        "has_tool_errors": has_tool_errors,
    }

    if components["final_check_failed"]:
        tier, score = "verified_failure", 0.05
    elif components["user_correction"] or components["user_retry"]:
        tier, score = "likely_failure", 0.20
    elif interrupted and not has_agent_final:
        tier, score = "likely_failure", 0.25
    elif components["final_check_passed"]:
        tier, score = "verified_success", 0.95
    elif approval:
        tier, score = "likely_success", 0.80
    elif has_agent_final and not has_tool_errors:
        tier, score = "likely_success", 0.65
    else:
        tier, score = "unclear", 0.50

    if components["fail_to_pass"] and tier == "verified_success":
        score = 1.0
    return tier, score, components
