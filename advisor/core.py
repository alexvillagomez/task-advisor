"""Shared task-advisor core.

Predicted pass chances come from the learned artifact.  The `_signals` and
`_rule_penalty` functions are deliberately hand-written: the trials do not
represent current-fact retrieval, ambiguous real work, or large codebases.
Keeping that boundary explicit makes the blend inspectable.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from .feedback import register_advice
from .training import DEFAULT_ARTIFACT, text_features


ROOT = Path(__file__).resolve().parents[1]
TIERS_PATH = Path(__file__).with_name("tiers.json")
PRICES_PATH = Path(__file__).with_name("prices.json")
VALID_STAKES = {"low", "medium", "high"}


@lru_cache(maxsize=1)
def _resources() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not DEFAULT_ARTIFACT.exists():
        raise RuntimeError("advisor artifact missing; run: python -m advisor train")
    artifact = json.loads(DEFAULT_ARTIFACT.read_text(encoding="utf-8"))
    tiers = json.loads(TIERS_PATH.read_text(encoding="utf-8"))
    prices = json.loads(PRICES_PATH.read_text(encoding="utf-8"))
    return artifact, tiers, prices


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-24.0, min(24.0, value))))


def _logit(value: float) -> float:
    value = max(0.001, min(0.999, value))
    return math.log(value / (1.0 - value))


def _signals(text: str) -> dict[str, Any]:
    lowered = text.lower()
    words = text.split()
    code = bool(re.search(r"```|\b(code|function|class|api|bug|debug|refactor|python|javascript|typescript|sql|repository|test suite)\b", lowered))
    external = bool(re.search(r"\b(latest|current|today|news|price|weather|browse|search|look up|website|documentation|cite|source)\b", lowered))
    ambiguity = bool(re.search(r"\b(something|somehow|etc\.?|make it good|improve this|best way|you know)\b", lowered))
    step_markers = len(re.findall(r"(?:^|\n)\s*(?:\d+[.)]|[-*])\s+|\b(?:then|after that|finally|first|second|multiple steps?)\b", lowered))
    multi_step = step_markers >= 2 or len(re.findall(r"\band\b", lowered)) >= 3
    numbers = len(re.findall(r"\b\d+(?:\.\d+)?\b", text))
    high_risk = bool(re.search(r"\b(medical|diagnos|patient|legal|lawsuit|contract|tax|invest|financial advice|safety critical|production database)\b", lowered))
    return {
        "code": code,
        "external": external,
        "ambiguity": ambiguity,
        "multi_step": multi_step,
        "long": len(words) > 180 or len(text) > 1400,
        "numbers": numbers,
        "high_risk": high_risk,
        "word_count": len(words),
        "line_count": text.count("\n") + 1,
    }


def _learned_probability(text: str, configuration: str, artifact: dict[str, Any]) -> float:
    model = artifact["models"][configuration]
    vocabulary = {token: index for index, token in enumerate(artifact["vocabulary"])}
    indices = [vocabulary[token] for token in text_features(text) if token in vocabulary]
    weights = model["weights"]
    score = float(model["intercept"]) + sum(float(weights[index]) for index in indices)
    score += float(weights[-2]) * math.log1p(len(text.split())) / 6.0
    score += float(weights[-1]) * math.log1p(text.count("\n") + 1) / 3.0
    return _sigmoid(score)


def _evidence(text: str, artifact: dict[str, Any]) -> tuple[str, float, str | None]:
    present = set(text_features(text))
    scores: list[tuple[float, str]] = []
    for family, signature in artifact["family_signatures"].items():
        hits = sum(token in present for token in signature)
        scores.append((float(hits), family))
    score, family = max(scores, default=(0.0, ""))
    normalized = min(1.0, score / 3.0)
    return ("low evidence" if normalized < 0.30 else "in distribution", normalized, family or None)


def _rule_penalty(signals: dict[str, Any]) -> float:
    """Rule-only difficulty correction, in log-odds units."""

    penalty = 0.0
    penalty += 0.45 if signals["code"] else 0.0
    penalty += 0.55 if signals["external"] else 0.0
    penalty += 0.50 if signals["ambiguity"] else 0.0
    penalty += 0.45 if signals["multi_step"] else 0.0
    penalty += 0.40 if signals["long"] else 0.0
    penalty += min(0.35, signals["numbers"] * 0.035)
    return penalty


def _reason_codes(signals: dict[str, Any], score: int) -> list[str]:
    candidates: list[str] = []
    if signals["code"]:
        candidates.append("code needs executable checks")
    if signals["external"]:
        candidates.append("needs current or external facts")
    if signals["multi_step"]:
        candidates.append("multiple dependent steps")
    if signals["ambiguity"]:
        candidates.append("requirements are underspecified")
    if signals["long"]:
        candidates.append("long context")
    if signals["numbers"] >= 3:
        candidates.append("numeric precision matters")
    if signals["high_risk"]:
        candidates.append("high-risk domain")
    candidates.append("learned pass rate is high" if score < 35 else "learned pass rate is limited")
    if len(candidates) < 2:
        candidates.insert(0, "short, direct request")
    return candidates[:4]


def _agent_setup(signals: dict[str, Any], complexity: int) -> str:
    if signals["code"]:
        return "coding agent with tests"
    if signals["external"]:
        return "tools/retrieval"
    if signals["multi_step"] or complexity >= 55:
        return "decompose"
    return "single call"


def _verification(stakes: str, pass_chance: float, signals: dict[str, Any]) -> str:
    if signals["high_risk"] and stakes == "high":
        return "human review"
    if signals["code"]:
        return "executable test"
    if stakes == "high" or pass_chance < 0.70:
        return "cross-model check"
    if stakes == "medium" or pass_chance < 0.90:
        return "self-check"
    return "none"


def advise(task_text: str, stakes: str = "medium", *, persist: bool = True) -> dict[str, Any]:
    """Return fast task routing, setup, verification, and confidence advice."""

    if not isinstance(task_text, str) or not task_text.strip():
        raise ValueError("task_text must be a non-empty string")
    stakes = stakes.lower()
    if stakes not in VALID_STAKES:
        raise ValueError("stakes must be low, medium, or high")
    artifact, tier_map, prices = _resources()
    signals = _signals(task_text)
    confidence_flag, evidence_score, nearest_family = _evidence(task_text, artifact)
    if signals["external"] or signals["high_risk"]:
        # Retrieval and consequential professional advice do not exist among
        # the twelve deterministic training families, even when words overlap.
        confidence_flag = "low evidence"
        evidence_score = min(evidence_score, 0.2)
    penalty = _rule_penalty(signals)
    tier_results: list[dict[str, Any]] = []
    mitigation = {"cheap": 1.0, "mid": 0.72, "frontier": 0.48}
    input_tokens = max(1, math.ceil(len(task_text) / 4))

    previous_chance = 0.0
    for tier_name in ("cheap", "mid", "frontier"):
        mapping = tier_map["tiers"][tier_name]
        config = mapping["learned_configuration"]
        learned = _learned_probability(task_text, config, artifact)
        adjusted = _sigmoid(_logit(learned) - penalty * mitigation[tier_name])
        if confidence_flag == "low evidence":
            # Unknown tasks are shrunk toward the observed configuration base
            # rate, then slightly discounted instead of claiming precision.
            base = float(artifact["models"][config]["base_rate"])
            adjusted = 0.58 * adjusted + 0.42 * base - 0.025
        # The public tiers express increasing capability, while the small
        # observed configurations contain sampling noise (terra medium is
        # slightly weaker in aggregate than terra low). Enforce only a modest
        # monotonic prior rather than allowing a "frontier" tier to look worse.
        if previous_chance:
            adjusted = max(adjusted, previous_chance + 0.025)
        adjusted = max(0.03, min(0.98, adjusted))
        previous_chance = adjusted
        backend = mapping["pricing_model"]
        rates = prices["models"][backend]
        mean_output = float(artifact["models"][config]["mean_output_tokens"])
        scale = max(0.55, min(2.4, 0.65 + signals["word_count"] / 220.0))
        output_tokens = max(40, round(mean_output * scale))
        cost = (input_tokens * float(rates["input"]) + output_tokens * float(rates["output"])) / 1_000_000
        tier_results.append({
            "model_tier": tier_name,
            "reasoning_effort": mapping["reasoning_effort"],
            "predicted_pass_chance": round(adjusted, 3),
            "rough_cost_usd": round(cost, 6),
        })

    target = {"low": 0.72, "medium": 0.88, "high": 0.95}[stakes]
    recommendation = next((item for item in tier_results if item["predicted_pass_chance"] >= target), tier_results[-1])
    frontier_pass = tier_results[-1]["predicted_pass_chance"]
    rule_score = min(1.0, penalty / 2.1)
    complexity_score = round(100 * (0.72 * (1.0 - frontier_pass) + 0.28 * rule_score))
    if signals["code"]:
        complexity_score = max(complexity_score, 36)
    if signals["external"]:
        complexity_score = max(complexity_score, 42)
    if signals["multi_step"]:
        complexity_score = max(complexity_score, 46)
    if signals["long"]:
        complexity_score = max(complexity_score, 50)
    if signals["high_risk"]:
        complexity_score = max(complexity_score, 55)
    complexity_score = max(0, min(100, complexity_score))
    label = "trivial" if complexity_score < 20 else "routine" if complexity_score < 45 else "hard" if complexity_score < 70 else "frontier"
    advice_id = uuid.uuid4().hex
    if persist:
        register_advice(advice_id, task_text)
    return {
        "advice_id": advice_id,
        "stakes": stakes,
        "complexity": {
            "score": complexity_score,
            "label": label,
            "reasons": _reason_codes(signals, complexity_score),
        },
        "recommendation": dict(recommendation),
        "tiers": tier_results,
        "agent_setup": _agent_setup(signals, complexity_score),
        "verification": _verification(stakes, float(recommendation["predicted_pass_chance"]), signals),
        "confidence": {
            "flag": confidence_flag,
            "evidence_score": round(evidence_score, 3),
            "nearest_training_family": nearest_family,
        },
    }

