"""Compatibility advice/MCP API; only bounded features and outcomes are stored."""
import math
import uuid
from .storage import connect, state_dir, setting, set_setting

_IDS = {}


def register_advice(advice_id, task_text):
    from .features import featurize
    with connect() as db:
        contexts = setting(db, "advice_context", {})
        try: identity = uuid.UUID(advice_id).hex
        except ValueError: identity = "legacy-" + str(len(contexts))
        contexts[identity] = featurize(task_text)
        set_setting(db, "advice_context", contexts)
    _IDS[advice_id] = identity


def append_feedback(advice_id, what_user_used, outcome, minutes_spent_fixing, cost_usd=None):
    if outcome not in {"pass", "partial", "fail"}: raise ValueError("outcome must be pass, partial, or fail")
    minutes = float(minutes_spent_fixing)
    cost = None if cost_usd in (None, "") else float(cost_usd)
    if not math.isfinite(minutes) or minutes < 0 or (cost is not None and (not math.isfinite(cost) or cost < 0)): raise ValueError("invalid minutes/cost")
    identity = _IDS.get(advice_id, advice_id)
    with connect() as db:
        context = setting(db, "advice_context", {}).get(identity)
        if context is None: raise ValueError("unknown advice id")
        tier = what_user_used.get("tier") if isinstance(what_user_used, dict) else what_user_used
        row = {"advice_id": identity, "features": context, "what_user_used": {"tier": tier if tier in {"cheap", "mid", "frontier"} else "unknown"}, "outcome": outcome, "minutes_spent_fixing": minutes, "cost_usd": cost}
        rows = setting(db, "feedback", [])
        rows.append(row)
        set_setting(db, "feedback", rows)
        return row


def read_feedback():
    with connect() as db: return setting(db, "feedback", [])
