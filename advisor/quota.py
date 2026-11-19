"""Quota shadow pricing, empirical slopes, and exploration budgets."""
from datetime import datetime
import math
import os
import time


def scarcity(used_pct, minutes_to_reset, window_minutes):
    # Compatibility arguments retained; reset proximity never discounts usage.
    remaining = max(1., 100.-used_pct)
    return (100./remaining)**2


def account_state(rows, engine, now=None):
    now = time.time() if now is None else now
    for r in reversed(rows):
        a = r["indicators"].get("account")
        if r["engine"] != engine or not a: continue
        result = {**a, "windows":{}}
        for k,w in a.get("windows",{}).items():
            reset = w.get("resets_at")
            if reset is not None and reset <= now: continue
            result["windows"][k] = {**w, "minutes_to_reset": max(0.,(reset-now)/60) if reset is not None else w.get("minutes_to_reset",0.)}
        if a.get("windows") and not result["windows"]: result["limit_reached"] = False
        return result
    return {}


def slopes(rows):
    totals, previous = {}, {}
    for r in rows:
        a = r["indicators"].get("account",{})
        engine = r["engine"]
        account_key = (engine,a.get("limit_id","unknown"))
        prev = previous.get(account_key)
        if prev:
            for name,w in a.get("windows",{}).items():
                old = prev.get("windows",{}).get(name,{})
                if w.get("resets_at") is None or w.get("resets_at") != old.get("resets_at"): continue
                delta = w.get("quota_used_pct",0)-old.get("quota_used_pct",0)
                tokens = r["indicators"].get("input_tokens",0)+r["indicators"].get("output_tokens",0)
                if 0 < delta <= 20 and tokens > 0:
                    key = engine+":"+r["model"]+":"+name
                    num,den = totals.get(key,(0.,0.))
                    totals[key] = num+delta,den+tokens
        if a: previous[account_key] = a
    return {k:n/d for k,(n,d) in totals.items()}


def price(option, f, rows, cfg, account=None):
    from .catalog import estimate
    same = [r for r in rows if r["engine"]==option["engine"] and r["model"]==option["id"] and r["effort"]==option["effort"]]
    observed = [r["indicators"] for r in same if r["indicators"].get("input_tokens") is not None and r["indicators"].get("output_tokens") is not None]
    tokens = sum(i["input_tokens"]+i["output_tokens"] for i in observed)/len(observed) if observed else max(60,(f.get("chars") or 240)/4)+{"low":300,"medium":600,"high":1200,"xhigh":1800,"max":2400}.get(option["effort"],300)
    latencies = [r["indicators"]["latency_ms"]/1000 for r in same if "latency_ms" in r["indicators"]]
    latency = sum(latencies)/len(latencies) if latencies else {"low":5.,"medium":15.,"high":40.}.get(option["effort"],60.)
    account = account if account is not None else account_state(rows,option["engine"])
    learned = slopes(rows)
    impacts, costs = [], []
    for name,w in account.get("windows",{}).items():
        impact = tokens*learned.get(option["engine"]+":"+option["id"]+":"+name,cfg["quota_pct_per_token"])
        impacts.append(impact)
        costs.append(impact*cfg["quota_dollar_per_pct"]*scarcity(w.get("quota_used_pct",0),w.get("minutes_to_reset",0),w.get("window_minutes",1)))
    fallback = tokens*cfg["quota_pct_per_token"]
    impact = max(impacts,default=fallback)
    # A shared request consumes both windows; price the binding (max) constraint.
    cost = estimate(option,f) if cfg["billing"]=="api" else max(costs,default=fallback*cfg["quota_dollar_per_pct"])
    return {"cost":cost,"api_cost":estimate(option,f),"quota_impact":impact,"latency":latency,"account":account,"quota_estimated":not bool(impacts)}


def budget(rows, cfg, engine, now=None):
    now = (now or datetime.now().astimezone()).astimezone()
    today = []
    for r in rows:
        if not r.get("time"): continue
        try: date = datetime.fromisoformat(r["time"]).astimezone().date()
        except ValueError: continue
        if date == now.date() and r["source"]=="live": today.append(r)
    spend = sum(r["indicators"].get("cost_usd",0) for r in today)
    used = sum(r["indicators"].get("quota_impact",0) for r in today if r["engine"]==engine)
    account = account_state(rows,engine,now.timestamp())
    if not account and engine == "codex" and not os.environ.get("PYTEST_CURRENT_TEST"):
        # First run: no live turn has recorded quota yet, so seed from the newest Codex rollout's
        # rate_limits (numbers only; manager fix 2026-11-19). Skipped under pytest for determinism.
        from .usage import latest_codex_account
        seed = latest_codex_account()
        if seed: account = account_state([{"engine":"codex","indicators":{"account":seed}}],engine,now.timestamp())
    stop =account.get("limit_reached",False) or any(w.get("quota_used_pct",0)>=cfg["quota_stop_pct"] for w in account.get("windows",{}).values())
    paused = spend >= cfg["daily_spend_cap"] or used >= cfg["daily_quota_cap"] or stop
    return {"paused":paused,"spend":spend,"quota":used,"account":account,
            "spend_left":max(0,cfg["daily_spend_cap"]-spend),"quota_left":max(0,cfg["daily_quota_cap"]-used)}
