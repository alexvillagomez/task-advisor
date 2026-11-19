"""Exercise the HTTP extension boundary without a network connection."""
from io import BytesIO
from types import SimpleNamespace
import json

import pytest

from advisor.features import featurize
from advisor.storage import events
from advisor.web import AdvisorHandler


def request(payload, origin="chrome-extension://test-id", method="POST", host="127.0.0.1:8765"):
    handler = AdvisorHandler.__new__(AdvisorHandler)
    body = json.dumps(payload).encode()
    handler.server = SimpleNamespace(server_port=8765)
    handler.path = "/api/ext/event"
    handler.headers = {"Host": host, "Origin": origin, "Content-Type": "application/json", "Content-Length": str(len(body))}
    handler.rfile, handler.wfile = BytesIO(body), BytesIO()
    response = {"headers": {}}
    handler.send_response = lambda code: response.update(status=code)
    handler.send_header = lambda k, v: response["headers"].update({k: v})
    handler.end_headers = lambda: None
    getattr(handler, "do_" + method)()
    response["body"] = json.loads(handler.wfile.getvalue())
    return response


@pytest.fixture
def payload(tmp_path, monkeypatch):
    monkeypatch.setenv("TASK_ADVISOR_HOME", str(tmp_path))
    return {"features": featurize("Write a short poem", fast=True),
            "recommendation": {"id": "gpt-6-luna", "effort": "low", "predicted": .8},
            "siteModel": "gpt-6.1-sol", "engine": "chatgpt", "rating": 1,
            "tags": ["v"], "source": "live", "client": "extension"}


@pytest.mark.parametrize("origin", ["chrome-extension://test-id", "moz-extension://test-id"])
def test_extension_stores_and_cors(payload, origin):
    response = request(payload, origin)
    assert response["status"] == 200
    assert response["headers"]["Access-Control-Allow-Origin"] == origin
    row, = events()
    assert (row["rating"], row["tags"], row["source"], row["engine"]) == (1, ["v"], "live", "chatgpt")
    assert row["features"]["previous_model"] == "gpt-6.1-sol"
    assert row["predicted"] == .8
    assert request({}, origin, "OPTIONS")["status"] == 200


@pytest.mark.parametrize("origin", ["https://chatgpt.com", "https://claude.ai", "http://127.0.0.1:8765", "null", ""])
def test_web_origin_rejected(payload, origin):
    assert request(payload, origin)["status"] == 403
    assert request({}, origin, "OPTIONS")["status"] == 403
    assert not events()


def test_extra_text_stripped(payload, tmp_path):
    secret = "private-never-store-182748"
    payload.update(prompt=secret, text=secret, url=secret, conversation_id=secret)
    payload["features"].update(prompt=secret, text=secret)
    payload["recommendation"]["text"] = secret
    payload["siteModel"] = secret
    assert request(payload)["status"] == 200
    for path in tmp_path.rglob("*"):
        if path.is_file(): assert secret.encode() not in path.read_bytes()


@pytest.mark.parametrize("field,value", [("tags", ["bad"]), ("tags", ["vo"]), ("tags", "v"), ("rating", True), ("rating", 2), ("engine", "web")])
def test_bad_feedback_rejected(payload, field, value):
    payload[field] = value
    assert request(payload)["status"] == 400
    assert not events()


def test_host_check_retained(payload):
    assert request(payload, host="evil.example:8765")["status"] == 403
    assert not events()


def test_claude_engine_mapping(payload):
    payload.update(engine="claude-web", recommendation={"id": "sonnet", "effort": "low", "predicted": .8})
    assert request(payload)["status"] == 200
    assert events()[0]["engine"] == "claude"
