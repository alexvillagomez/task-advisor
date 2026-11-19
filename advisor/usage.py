"""Allowlisted account telemetry. Never read credentials or persist rollout text."""
from datetime import datetime, timezone
import json
import os
import math
from pathlib import Path
import re
import time

PLANS = {"free", "plus", "pro", "prolite", "go", "team", "business", "enterprise", "edu", "unknown"}


def number(v):
    return float(v) if type(v) in (int,float) and math.isfinite(v) and v >= 0 else None


def rate_limits(value, now=None):
    if not isinstance(value,dict): return {}
    now = time.time() if now is None else now
    result = {"plan_type": value.get("plan_type") if value.get("plan_type") in PLANS else "unknown"}
    result["limit_id"] = value.get("limit_id") if value.get("limit_id") in {"codex", "claude", "gpt-6-astra", "gpt-6.1-terra", "gpt-6.1-sol", "gpt-6-luna"} else "unknown"
    windows = {}
    for name in ("primary", "secondary"):
        v = value.get(name)
        if not isinstance(v,dict): continue
        w = {}
        for source,target in (("used_percent","quota_used_pct"),("window_minutes","window_minutes")):
            n = number(v.get(source))
            if n is not None: w[target] = min(100.,n) if source == "used_percent" else n
        reset = number(v.get("resets_at"))
        if reset is not None:
            w.update(resets_at=reset, minutes_to_reset=max(0.,(reset-now)/60))
        if w: windows[name] = w
    result["windows"] = windows
    credits = value.get("credits") or {}
    if isinstance(credits,dict):
        for k in ("has_credits","unlimited"):
            if type(credits.get(k)) is bool: result[k] = credits[k]
        balance = credits.get("balance")
        if isinstance(balance,str):
            try: balance = float(balance)
            except ValueError: balance = None
        if number(balance) is not None: result["credit_balance"] = number(balance)
    result["limit_reached"] = bool(value.get("rate_limit_reached_type") or value.get("spend_control_reached"))
    return result


def clean_account(v):
    if not isinstance(v,dict): return {}
    raw = {"plan_type":v.get("plan_type"), "limit_id":v.get("limit_id"),
           "credits":{"has_credits":v.get("has_credits"),"unlimited":v.get("unlimited"),"balance":v.get("credit_balance")},
           "rate_limit_reached_type": v.get("limit_reached") is True}
    for k,w in v.get("windows",{}).items() if isinstance(v.get("windows"),dict) else []:
        if k in {"primary","secondary"} and isinstance(w,dict):
            raw[k] = {"used_percent":w.get("quota_used_pct"),"window_minutes":w.get("window_minutes"),"resets_at":w.get("resets_at")}
    clean = rate_limits(raw)
    # Preserve observed reset duration, instead of changing history on every re-rating.
    for k,w in clean["windows"].items():
        original = v.get("windows",{}).get(k,{})
        if number(original.get("minutes_to_reset")) is not None: w["minutes_to_reset"] = number(original["minutes_to_reset"])
    return clean


def extract(record):
    payload = record.get("payload") or {}
    if not isinstance(payload,dict): payload = {}
    raw = record.get("rate_limits") or payload.get("rate_limits")
    return rate_limits(raw) if raw else {}


def rollout_usage(thread_id, root=None, since=None):
    # UUID-only glob input prevents arbitrary paths. File names are never returned.
    if not re.fullmatch(r"[a-fA-F0-9-]{32,36}", thread_id or ""): return {}
    root = Path(root or Path.home()/".codex/sessions")
    latest = {}
    before, after = {}, {}
    for path in root.rglob("*"+thread_id+"*.jsonl"):
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    try: record = json.loads(line)
                    except ValueError: continue
                    if not isinstance(record,dict): continue
                    payload = record.get("payload") or {}
                    info = payload.get("info") or {} if isinstance(payload,dict) else {}
                    total = info.get("total_token_usage") or {} if isinstance(info,dict) else {}
                    if since is not None:
                        try:
                            stamp = datetime.fromisoformat(record.get("timestamp","").replace("Z","+00:00")).timestamp()
                        except (ValueError,TypeError): continue
                        if stamp < since:
                            if isinstance(total,dict) and total: before = total
                            continue
                    if isinstance(total,dict) and total: after = total
                    account = extract(record)
                    if account: latest["account"] = account
                    usage = info.get("last_token_usage") or {} if isinstance(info,dict) else {}
                    for src,dst in (("input_tokens","input_tokens"),("output_tokens","output_tokens"),("cached_input_tokens","cached_tokens"),("reasoning_output_tokens","reasoning_tokens")):
                        if number(usage.get(src)) is not None: latest[dst] = number(usage[src])
        except OSError: continue
    # Cumulative session deltas recover all API/tool cycles of a resumed turn.
    # They supersede last_token_usage when both totals are available. A fresh
    # thread has no earlier total, so its cumulative total is the turn total.
    for src,dst in (("input_tokens","input_tokens"),("output_tokens","output_tokens"),("cached_input_tokens","cached_tokens"),("reasoning_output_tokens","reasoning_tokens")):
        end, start = number(after.get(src)), number(before.get(src,0))
        if end is not None and start is not None and end >= start: latest[dst] = end-start
    return latest


def latest_codex_account(root=None, max_files=3):
    """Most recent rate_limits snapshot from the newest Codex rollout files (numbers only, no text kept)."""
    root = Path(root or os.environ.get("CODEX_HOME") or Path.home()/".codex")/"sessions"
    try:
        days = sorted((d for d in root.glob("*/*/*") if d.is_dir()), reverse=True)[:2]
        files = sorted((f for d in days for f in d.glob("rollout-*.jsonl")), key=lambda f: f.stat().st_mtime, reverse=True)[:max_files]
    except OSError:
        return {}
    for path in files:
        found = {}
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    if "rate_limits" not in line: continue
                    try: record = json.loads(line)
                    except ValueError: continue
                    if isinstance(record, dict):
                        account = extract(record)
                        if account: found = account
        except OSError:
            continue
        if found: return found
    return {}
