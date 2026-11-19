import json
import math
from pathlib import Path
import random
import sys
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from advisor.catalog import options
from advisor.chat import chat
from advisor.engines import parse_output
from advisor.features import featurize
from advisor.labels import combine, parse_rating
from advisor.policy import choose, learn
from advisor.priors import export_update, merge_updates, use_prior, evaluate
from advisor.storage import add, connect, events, make_event, revise, setting

CANARY = "PRIVATE_CANARY_839201_alex_secret_do_not_store"


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "state"
    monkeypatch.setenv("TASK_ADVISOR_HOME", str(path))
    return path


def assert_private(home):
    for path in home.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert CANARY.encode() not in data
            assert b"redacted_prompt" not in data
            assert b"prompt_hash" not in data


def event(**kwargs):
    return make_event(featurize("Write a Python function"), "codex", "gpt-6.1-terra", "medium", **kwargs)


@pytest.mark.parametrize("value,expected", [("", (None, [])), ("+", (1, [])), ("-vw", (0, ["v", "w"])), ("+oo", (1, ["o"]))])
def test_rating(value, expected):
    parsed = parse_rating(value)
    assert (parsed["rating"], parsed["tags"]) == expected


@pytest.mark.parametrize("value", ["yes", "-x", "+wrong", "--"])
def test_rating_rejects_text(value):
    with pytest.raises(ValueError): parse_rating(value)


def test_labels():
    assert combine({"exit_status": 1, "tests_failed": 3}, 1)["reward"] == 1
    assert combine({"tests_passed": 1}, 0)["reward"] == 0
    assert combine({})["source"] == "unclear"
    assert combine({"tests_passed": 1})["reward"] > .9
    assert combine({"correction": True})["reward"] < .2
    assert combine({"outcome_tier": "verified_success", "outcome_score": 1., "tests_failed": 1, "tests_passed": 1})["reward"] == 1.


def test_bandit_cheapest_and_high_stakes(home):
    f = featurize("hello", "low")
    f["base_predictions"] = dict(cheap=.9, mid=.93, frontier=.96)
    selected = choose(f, "codex", exploration=0, weights={}, objective="threshold")
    assert selected["id"] == "gpt-6-luna" and selected["effort"] == "low"
    assert selected["propensity"] == 1
    f["stakes"] = "high"
    class NoRandom:
        def random(self): raise AssertionError("explored on high stakes")
    assert choose(f, "codex", exploration=1, rng=NoRandom(), weights={})["propensity"] == 1


def test_exploration_floor_and_probability(home):
    f = featurize("hello", "low")
    f["base_predictions"] = dict(cheap=.1, mid=.6, frontier=.8)
    rng = random.Random(3)
    for _ in range(100):
        result = choose(f, "codex", exploration=1, rng=rng, weights={})
        assert result["tier"] != "cheap"
        assert result["propensity"] == pytest.approx(1/9)
    f["base_predictions"]["cheap"] = .9
    class Greedy:
        def random(self): return .99
    pick = choose(f, "codex", rng=Greedy(), weights={}, objective="threshold")
    assert .9 < pick["propensity"] <= 1  # greedy keeps 1-eps plus its share of the cost-capped pool
    assert pick["cost"] * 4 >= 0 and pick["why"] == "cheapest above target"


@pytest.mark.parametrize("engine,models", [("codex", ["gpt-6-luna", "gpt-6.1-terra"]), ("claude", ["haiku", "opus"])])
def test_chat_real_subprocess_session_switch_privacy(home, tmp_path, monkeypatch, engine, models):
    ledger = tmp_path/"args.jsonl"
    monkeypatch.setenv("FAKE_ENGINE_ARGS", str(ledger))
    lines = iter(["/model "+models[0], CANARY, "+o", "/model "+models[1], "No, wrong, " + CANARY, "-vw", "/why", "/rate", "+", "/quit"])
    output = []
    chat(engine, input_fn=lambda _: next(lines), output=output.append, executable=[sys.executable, str(Path(__file__).with_name("fake_engine.py"))])
    records = events()
    assert len(records) == 2
    assert [r["model"] for r in records] == models
    assert records[0]["indicators"]["correction"]
    assert records[1]["rating"] == 1
    args = [json.loads(l) for l in ledger.read_text().splitlines()]
    assert ("resume" if engine == "codex" else "--resume") in args[1]
    assert all(CANARY not in str(a) for a in args)
    export_update(); evaluate(True)
    assert_private(home)


def test_rerate_replaces_training_and_tags_affect_selection(home):
    row = add(event())
    revise(row["id"], rating=parse_rating("+o"))
    with connect() as db: first = setting(db, "personal")
    assert any("gpt-6-luna" in k for k in first)
    revise(row["id"], rating=parse_rating("-u"))
    rows = events()
    with connect() as db: weights = setting(db, "personal")
    assert weights == learn(rows, {})[0]
    assert len(rows) == 1 and weights != first


def test_export_merge_count_weighted_and_roundtrip(home, tmp_path):
    row = add(event()); revise(row["id"], rating=parse_rating("+"))
    directory = tmp_path/"updates"
    exported = export_update(directory/"one.json")
    a = json.loads(exported.read_text())
    b = json.loads(exported.read_text())
    for k in b["deltas"]:
        b["deltas"][k] = [v/2 for v in b["deltas"][k]]
        b["counts"][k] *= 3
    (directory/"two.json").write_text(json.dumps(b))
    merged = merge_updates(directory, tmp_path/"team.json")
    c = json.loads(merged.read_text())
    for k in c["deltas"]:
        assert c["deltas"][k] == pytest.approx([v*.625 for v in a["deltas"][k]])
    use_prior(merged)
    with connect() as db: assert setting(db, "team") == c["deltas"]
    assert set(evaluate()["tiers"]) == {"personal", "team", "baseline"}
    assert_private(home)


def test_invalid_prior_is_not_installed(home, tmp_path):
    path = tmp_path/"invalid.json"
    path.write_text(json.dumps({"version": "advisor-weights-v2", "deltas": {CANARY: [0]*13}, "counts": {CANARY: 1}}))
    with pytest.raises(ValueError): use_prior(path)
    assert_private(home)


def test_engine_indicators_are_structured():
    option = options("codex")[0]
    output = '\n'.join(json.dumps(r) for r in [
        {"type": "item.started", "item": {"id": "a", "type": "command_execution", "command": "pytest"}},
        {"type": "item.completed", "item": {"id": "a", "exit_code": 1, "status": "failed"}},
        {"type": "turn.failed", "error": {"message": CANARY}}])
    _, _, indicators = parse_output(output, "codex", 0, 2, option)
    assert indicators["tests_failed"] == 1 and indicators["command_failures"] == 1
    assert indicators["exit_status"] == 1
    assert CANARY not in json.dumps(indicators)


def test_claude_verbose_tools_require_structured_exit_status():
    option = options("claude")[0]
    records = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "pytest"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "is_error": False, "content": CANARY}]}, "toolUseResult": {"exitCode": 1}},
        {"type": "result", "result": "ok", "session_id": "test", "usage": {"input_tokens": 10, "output_tokens": 20}},
    ]
    answer, session, ind = parse_output(json.dumps(records), "claude", 0, 1, option)
    assert answer == "ok" and session == "test"
    assert ind["tests_failed"] == 1 and ind["tests_passed"] == 0
    assert CANARY not in json.dumps(ind)


def test_prequential_eval_and_backlog_downweight(home):
    row = add(event()); revise(row["id"], rating=parse_rating("-"))
    metrics = evaluate()["tiers"]
    assert metrics["personal"]["log_loss"] == metrics["baseline"]["log_loss"]
    live = events()[0]
    historical = {**live, "source": "backlog"}
    live_counts = learn([live], {})[1]
    backlog_counts = learn([historical], {})[1]
    for key in live_counts: assert backlog_counts[key] == live_counts[key]*.25


def test_forget_and_no_unrelated_file_deletion(home):
    from advisor.cli import run
    add(event()); export_update(); evaluate(True)
    export_update(home/"custom-update.json")
    evaluate(str(home/"custom-eval.json"))
    unrelated = home/"keep.txt"; unrelated.write_text("user-owned")
    assert run(["forget"]) == 0
    assert list(home.iterdir()) == [unrelated]


def test_dashboard_rate_and_csrf(home):
    from advisor.web import AdvisorHandler, ThreadingHTTPServer
    row = add(event())
    server = ThreadingHTTPServer(("127.0.0.1", 0), AdvisorHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base+"/api/turns") as response: assert json.load(response)["recent"][0]["id"] == row["id"]
        data = json.dumps({"id": row["id"], "rating": "+o"}).encode()
        with urlopen(Request(base+"/api/rate", data=data, headers={"Content-Type": "application/json"})) as response: assert json.load(response)["tags"] == ["o"]
        with pytest.raises(HTTPError) as error:
            urlopen(Request(base+"/api/rate", data=data, headers={"Content-Type": "application/json", "Origin": "https://evil.example"}))
        assert error.value.code == 403
    finally:
        server.shutdown(); server.server_close(); worker.join()
