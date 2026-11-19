from __future__ import annotations

from advisor.core import advise


def test_advise_returns_all_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("ADVISOR_STATE_DIR", str(tmp_path))
    result = advise("Write a Python function that sorts records and include unit tests.")
    assert result["advice_id"]
    assert result["stakes"] == "medium"
    assert 0 <= result["complexity"]["score"] <= 100
    assert result["complexity"]["label"] in {"trivial", "routine", "hard", "frontier"}
    assert 2 <= len(result["complexity"]["reasons"]) <= 4
    assert result["recommendation"]["model_tier"] in {"cheap", "mid", "frontier"}
    assert result["recommendation"]["reasoning_effort"] in {"low", "medium", "high"}
    assert 0 <= result["recommendation"]["predicted_pass_chance"] <= 1
    assert result["recommendation"]["rough_cost_usd"] >= 0
    assert len(result["tiers"]) == 3
    assert result["agent_setup"] in {"single call", "decompose", "tools/retrieval", "coding agent with tests"}
    assert result["verification"] in {"none", "self-check", "cross-model check", "executable test", "human review"}
    assert result["confidence"]["flag"] in {"low evidence", "in distribution"}


def test_high_stakes_never_has_no_verification(tmp_path, monkeypatch):
    monkeypatch.setenv("ADVISOR_STATE_DIR", str(tmp_path))
    result = advise("Say hello.", stakes="high")
    assert result["verification"] != "none"

