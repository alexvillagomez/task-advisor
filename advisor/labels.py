"""Versioned explicit and weak-label combiner. No free-form fields."""
VERSION = "advisor-label-v2.0"
TAGS = {"v": "needed verification", "o": "overkill", "u": "underpowered", "w": "wrong", "p": "partial", "s": "too slow", "c": "too costly"}
TIERS = {"verified_success", "verified_failure", "likely_success", "likely_failure", "unclear"}


def parse_rating(value: str) -> dict:
    value = value.strip().lower()
    if not value:
        return {"rating": None, "tags": []}
    if value in {"a", "b", "="}:
        return {"rating": None, "tags": [], "preference": {"a":1.,"b":0.,"=":.5}[value]}
    if value[0] not in "+-" or any(c not in TAGS for c in value[1:]):
        raise ValueError("Use + or -, optional tags v o u w p s c typed together, or Enter to skip")
    return {"rating": 1 if value[0] == "+" else 0, "tags": sorted(set(value[1:]))}


def combine(indicators: dict, rating=None, tags=()) -> dict:
    if rating is not None:
        reward, source, confidence = float(rating), "explicit", 1.
    elif indicators.get("outcome_tier") in TIERS:
        # Ingest already composes final checks, repairs and follow-ups. Do not
        # count its component indicators again (e.g. an earlier failed test).
        tier = indicators["outcome_tier"]
        reward = indicators.get("outcome_score", .5)
        source = "unclear" if tier == "unclear" else "weak"
        confidence = .85 if tier.startswith("verified") else .4 if tier != "unclear" else .05
    else:
        signals = []
        if indicators.get("exit_status") not in (None, 0): signals.append((0., 1.))
        if indicators.get("tests_failed", 0): signals.append((.05, .95))
        elif indicators.get("tests_passed", 0): signals.append((.95, .9))
        if indicators.get("correction"): signals.append((.1, .8))
        if indicators.get("retry"): signals.append((.2, .65))
        if indicators.get("tool_failures", 0) or indicators.get("command_failures", 0): signals.append((.25, .5))
        if not signals:
            reward, source, confidence = .5, "unclear", .05
        else:
            reward = sum(v*w for v, w in signals) / sum(w for _, w in signals)
            source, confidence = "weak", max(w for _, w in signals)
    return {"version": VERSION, "reward": max(0., min(1., reward)), "source": source, "confidence": confidence}
