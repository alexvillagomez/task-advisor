from __future__ import annotations

from advisor.feedback import append_feedback, read_feedback, register_advice


def test_feedback_round_trip_discards_text(tmp_path, monkeypatch):
    monkeypatch.setenv("ADVISOR_STATE_DIR", str(tmp_path))
    register_advice("advice-1", "Email alex@private.example about password=hunter2")
    written = append_feedback("advice-1", {"tier": "mid", "note": "token=sk-secretvalue123"}, "partial", 4.5, 0.012)
    loaded = read_feedback()
    assert loaded == [written]
    # v0.2 intentionally removes v0.1's content-bearing compatibility fields.
    assert "prompt_hash" not in loaded[0]
    assert "redacted_prompt" not in loaded[0]
    assert loaded[0]["what_user_used"]["tier"] == "mid"
    assert "note" not in loaded[0]["what_user_used"]
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert b"hunter2" not in path.read_bytes()
            assert b"secretvalue123" not in path.read_bytes()

