"""Minimal newline-delimited JSON-RPC MCP server over stdio."""

from __future__ import annotations

import json
import sys
from typing import IO, Any

from .core import advise
from .feedback import append_feedback


TOOLS = [
    {
        "name": "advise_task",
        "description": "Estimate task complexity and recommend a model tier, agent setup, and verification.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_text": {"type": "string"},
                "stakes": {"type": "string", "enum": ["low", "medium", "high"], "default": "medium"},
            },
            "required": ["task_text"],
        },
    },
    {
        "name": "report_outcome",
        "description": "Record how an advised task went without storing its raw prompt.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "advice_id": {"type": "string"},
                "what_user_used": {"type": ["string", "object"]},
                "outcome": {"type": "string", "enum": ["pass", "partial", "fail"]},
                "minutes_spent_fixing": {"type": "number", "minimum": 0},
                "cost_usd": {"type": ["number", "null"], "minimum": 0},
            },
            "required": ["advice_id", "what_user_used", "outcome", "minutes_spent_fixing"],
        },
    },
]


def _result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(request: dict[str, Any]) -> dict[str, Any] | None:
    request_id = request.get("id")
    method = request.get("method")
    if request_id is None:  # notifications, including notifications/initialized
        return None
    if method == "initialize":
        return _result(request_id, {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "local-task-advisor", "version": "1.0.0"},
        })
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = request.get("params", {})
        name, arguments = params.get("name"), params.get("arguments", {})
        try:
            if name == "advise_task":
                payload = advise(arguments["task_text"], arguments.get("stakes", "medium"))
            elif name == "report_outcome":
                payload = append_feedback(
                    arguments["advice_id"], arguments["what_user_used"], arguments["outcome"],
                    arguments["minutes_spent_fixing"], arguments.get("cost_usd"),
                )
            else:
                return _error(request_id, -32602, f"unknown tool: {name}")
            return _result(request_id, {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                "structuredContent": payload,
            })
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            return _result(request_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
    return _error(request_id, -32601, f"method not found: {method}")


def run_mcp(stdin: IO[str] = sys.stdin, stdout: IO[str] = sys.stdout) -> None:
    for line in stdin:
        if not line.strip():
            continue
        try:
            response = handle(json.loads(line))
        except (TypeError, json.JSONDecodeError) as exc:
            response = _error(None, -32700, f"parse error: {exc}")
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            stdout.flush()

