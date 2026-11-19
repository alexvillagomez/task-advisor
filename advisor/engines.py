"""CLI adapter. Text/session IDs stay in RAM, never in advisor state."""
import json
import shutil
import subprocess
import time
from ingest.signals import detect_check_command


def command(engine, model, effort, session=None, executable=None, *, readonly=False):
    exe = executable or shutil.which(engine)
    if not exe: raise ValueError("Engine CLI is not installed or not on PATH")
    prefix = list(exe) if isinstance(exe, (tuple, list)) else [exe]
    if engine == "codex":
        if readonly:
            # Fresh context avoids contaminating A's resumed thread with B. No
            # configured external MCP tools or hooks are loaded for a shadow.
            return prefix + ["exec", "-s", "read-only", "--ignore-user-config", "--ephemeral", "-c", "mcp_servers={}", "-c", "features.hooks=false", "--json", "--skip-git-repo-check", "-m",model,"-c","model_reasoning_effort="+effort,"-"]
        return prefix + ["exec"] + (["resume", session] if session else []) + ["--json", "--skip-git-repo-check", "-m", model, "-c", "model_reasoning_effort=" + effort, "-"]
    if readonly:
        return prefix + ["-p", "--output-format", "json", "--verbose", "--model",model,"--effort",effort,
                         "--permission-mode","plan","--tools","", "--strict-mcp-config","--mcp-config",'{"mcpServers":{}}',
                         "--setting-sources","", "--settings",'{"disableAllHooks":true}',"--disable-slash-commands","--no-chrome","--no-session-persistence"]
    return prefix + ["-p", "--output-format", "json", "--verbose", "--model", model, "--effort", effort] + (["--resume", session] if session else [])


def parse_output(stdout, engine, exit_status, elapsed, option):
    answer, session = [], None
    indicators = {"exit_status": exit_status, "latency_ms": round(elapsed*1000), "tool_failures": 0, "command_failures": 0, "tests_passed": 0, "tests_failed": 0}
    try:
        value = json.loads(stdout)
        records = value if isinstance(value, list) else [value]
    except ValueError:
        records = []
        for line in stdout.splitlines():
            try: records.append(json.loads(line))
            except ValueError: continue
    seen, pending = set(), {}
    claude_calls = {}
    indicators["tool_calls"] = 0
    from .usage import extract, number
    for record in records:
        if not isinstance(record, dict): continue
        kind = record.get("type")
        account = extract(record)
        if account: indicators["account"] = account
        payload = record.get("payload") or {}
        info = payload.get("info") or {} if isinstance(payload,dict) else {}
        token_usage = info.get("last_token_usage") or {} if isinstance(info,dict) else {}
        for src,dst in (("input_tokens","input_tokens"),("output_tokens","output_tokens"),("cached_input_tokens","cached_tokens"),("reasoning_output_tokens","reasoning_tokens")):
            if number(token_usage.get(src)) is not None: indicators[dst] = number(token_usage[src])
        session = record.get("thread_id") or record.get("session_id") or session
        usage = record.get("usage")
        if isinstance(usage, dict):
            for key, target in (("input_tokens", "input_tokens"), ("output_tokens", "output_tokens"), ("cached_input_tokens", "cached_tokens"), ("cache_read_input_tokens","cached_tokens"), ("cache_creation_input_tokens","cache_creation_tokens"), ("reasoning_output_tokens","reasoning_tokens")):
                if isinstance(usage.get(key), (int, float)): indicators[target] = max(0, usage[key])
            details = usage.get("output_tokens_details") or {}
            if isinstance(details,dict) and number(details.get("thinking_tokens")) is not None: indicators["reasoning_tokens"] = number(details["thinking_tokens"])
        # Some Claude versions expose only modelUsage (model names never persist).
        model_usage = record.get("modelUsage")
        if isinstance(model_usage,dict) and not isinstance(usage,dict):
            for src,dst in (("inputTokens","input_tokens"),("outputTokens","output_tokens"),("cacheReadInputTokens","cached_tokens"),("cacheCreationInputTokens","cache_creation_tokens")):
                values = [number(v.get(src)) for v in model_usage.values() if isinstance(v,dict)]
                if any(v is not None for v in values): indicators[dst] = sum(v or 0 for v in values)
        if isinstance(record.get("cost_usd"), (int,float)): indicators["cost_usd"], indicators["cost_estimated"] = max(0,record["cost_usd"]),False
        if isinstance(record.get("total_cost_usd"), (int, float)):
            indicators["cost_usd"], indicators["cost_estimated"] = max(0, record["total_cost_usd"]), False
        if kind == "result":
            if isinstance(record.get("result"), str): answer.append(record["result"])
            if record.get("is_error") or str(record.get("subtype", "")).startswith("error"): indicators["exit_status"] = exit_status or 1
        if kind in {"error", "turn.failed"}: indicators["exit_status"] = exit_status or 1
        message = record.get("message") or {}
        content = message.get("content", []) if isinstance(message, dict) else []
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict): continue
                if block.get("type") == "tool_use":
                    indicators["tool_calls"] += int(block.get("id") not in claude_calls)
                    inputs = block.get("input") or {}
                    claude_calls[block.get("id")] = detect_check_command(inputs.get("command") or inputs.get("cmd")) if isinstance(inputs, dict) else None
                elif block.get("type") == "tool_result":
                    identity = block.get("tool_use_id")
                    if identity in seen: continue
                    seen.add(identity)
                    failed = block.get("is_error") is True
                    structured = record.get("toolUseResult") or {}
                    code = structured.get("exitCode", structured.get("exit_code")) if isinstance(structured, dict) else None
                    indicators["tool_failures"] += int(failed)
                    indicators["command_failures"] += int(code is not None and code != 0)
                    if claude_calls.get(identity):
                        if code not in (None, 0) or failed: indicators["tests_failed"] += 1
                        elif code == 0: indicators["tests_passed"] += 1
                        # Tool-call success alone is not evidence that tests passed.
        item = record.get("item") or {}
        if kind == "item.started" and isinstance(item, dict): pending[item.get("id")] = item
        if kind in {"item.completed", "item.failed"} and isinstance(item, dict):
            identity = item.get("id")
            if identity and identity in seen: continue
            if identity: seen.add(identity)
            item = {**pending.get(identity, {}), **item}
            if item.get("type") == "agent_message":
                if isinstance(item.get("text"), str): answer.append(item["text"])
            elif item.get("type") in {"command_execution", "mcp_tool_call", "file_change"}:
                indicators["tool_calls"] += 1
                code = item.get("exit_code")
                failed = kind == "item.failed" or item.get("status") in {"failed", "error"} or item.get("is_error") is True
                indicators["tool_failures"] += int(failed)
                indicators["command_failures"] += int(code is not None and code != 0)
                if detect_check_command(item.get("command")):
                    if code == 0: indicators["tests_passed"] += 1
                    elif code is not None or failed: indicators["tests_failed"] += 1
    if "cost_usd" not in indicators:
        indicators["cost_usd"] = (indicators.get("input_tokens", 0)*option["input"] + indicators.get("output_tokens", 0)*option["output"]) / 1e6
        indicators["cost_estimated"] = True
    if not records: indicators["exit_status"] = exit_status or 1
    indicators["tests_run"] = indicators["tests_passed"]+indicators["tests_failed"]
    return "\n".join(answer), session, indicators


def run(engine, option, prompt, session=None, cwd=None, executable=None, *, readonly=False):
    started = time.monotonic()
    wall_started = time.time()
    try:
        result = subprocess.run(command(engine, option["id"], option["effort"], session, executable, readonly=readonly), input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd, check=False)
    except OSError:
        raise ValueError("Engine CLI could not start; check installation and working directory") from None
    answer, returned, indicators = parse_output(result.stdout, engine, result.returncode, time.monotonic()-started, option)
    if engine == "codex" and returned:
        from .usage import rollout_usage
        extra = rollout_usage(returned,since=wall_started)
        for k,v in extra.items(): indicators.setdefault(k,v)
        if indicators.get("cost_estimated"):
            indicators["cost_usd"] = (indicators.get("input_tokens",0)*option["input"]+indicators.get("output_tokens",0)*option["output"])/1e6
    indicators["context_mode"] = "fresh" if readonly or not session else "resumed"
    return answer, returned or session, indicators
