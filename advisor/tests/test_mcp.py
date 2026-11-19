from __future__ import annotations

import io
import json

from advisor.mcp import run_mcp


def test_mcp_initialize_list_and_call(tmp_path, monkeypatch):
    monkeypatch.setenv("ADVISOR_STATE_DIR", str(tmp_path))
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "advise_task", "arguments": {"task_text": "Add 2 and 3", "stakes": "low"}}},
    ]
    fake_input = io.StringIO("".join(json.dumps(item) + "\n" for item in requests))
    fake_output = io.StringIO()
    run_mcp(fake_input, fake_output)
    responses = [json.loads(line) for line in fake_output.getvalue().splitlines()]
    assert responses[0]["result"]["serverInfo"]["name"] == "local-task-advisor"
    assert {tool["name"] for tool in responses[1]["result"]["tools"]} == {"advise_task", "report_outcome"}
    assert responses[2]["result"]["structuredContent"]["advice_id"]

