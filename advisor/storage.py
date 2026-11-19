"""Transactional local state with an explicit, content-free write boundary."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import uuid

from .features import validate, migrate
from .labels import TAGS, TIERS, combine
from .catalog import models

NUMBERS = {"latency_ms", "input_tokens", "output_tokens", "cached_tokens", "cost_usd", "exit_status", "tool_failures", "command_failures", "tests_passed", "tests_failed", "retry_similarity", "outcome_score"}
NUMBERS |= {"reasoning_tokens", "cache_creation_tokens", "tool_calls", "tests_run", "quota_impact"}
FLAGS = {"retry", "correction", "manual_override", "regeneration", "user_edit", "cost_estimated"}
FLAGS |= {"shadow", "collection", "quota_estimated"}


def state_dir():
    return Path(os.environ.get("TASK_ADVISOR_HOME") or os.environ.get("ADVISOR_STATE_DIR") or Path.home() / ".task-advisor")


@contextmanager
def connect():
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(directory / "advisor.sqlite3", timeout=30)
    try:
        db.execute("PRAGMA secure_delete=ON")
        db.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, row TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS manifest (identity TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS pairs (a TEXT PRIMARY KEY, b TEXT NOT NULL UNIQUE, preference REAL, propensity REAL NOT NULL)")
        with db:
            if db.execute("PRAGMA user_version").fetchone()[0] < 3:
                for identity, raw in db.execute("SELECT id,row FROM events").fetchall():
                    row = json.loads(raw)
                    row["features"] = migrate(row["features"])
                    row["schema"] = 3
                    db.execute("UPDATE events SET row=? WHERE id=?",(json.dumps(row,allow_nan=False),identity))
                db.execute("PRAGMA user_version=3")
            yield db
    finally:
        db.close()


def clean_indicators(values):
    result = {}
    for k in NUMBERS:
        v = values.get(k)
        if v is not None:
            if type(v) not in (int, float) or not math.isfinite(v) or (k != "exit_status" and v < 0):
                raise ValueError("invalid numeric indicator")
            result[k] = v
    for k in FLAGS:
        if k in values:
            if type(values[k]) is not bool: raise ValueError("invalid boolean indicator")
            result[k] = values[k]
    if values.get("outcome_tier") in TIERS:
        result["outcome_tier"] = values["outcome_tier"]
    if "account" in values:
        from .usage import clean_account
        result["account"] = clean_account(values["account"])
    if "selection_probabilities" in values:
        from .catalog import options
        probabilities = values["selection_probabilities"]
        allowed = {o["key"] for o in options()}
        if not isinstance(probabilities,dict) or not set(probabilities)<=allowed or any(type(v) not in (float,int) or not math.isfinite(v) or not 0 < v <= 1 for v in probabilities.values()) or not math.isclose(sum(probabilities.values()),1.,abs_tol=1e-9):
            raise ValueError("invalid selection distribution")
        result["selection_probabilities"] = dict(probabilities)
    if values.get("context_mode") in {"fresh", "resumed", "fresh_replay"}: result["context_mode"] = values["context_mode"]
    return result


def make_event(features, engine, model, effort, indicators=None, *, source="live", timestamp=None, propensity=None, predicted=None, override=False):
    features = validate(migrate(features))
    if engine not in {"codex", "claude", "chatgpt"} or source not in {"live", "backlog"}: raise ValueError("invalid source")
    allowed = {m["id"] for m in models()}
    model = model if model in allowed else "unknown"
    effort = effort if effort in {"low", "medium", "high", "xhigh", "max", "none"} else "unknown"
    if timestamp is not None:
        timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).isoformat()
    for v in (propensity, predicted):
        if v is not None and (type(v) not in (float, int) or not 0 <= v <= 1): raise ValueError("invalid probability")
    indicators = clean_indicators(indicators or {})
    indicators["manual_override"] = bool(override)
    return {"id": uuid.uuid4().hex, "schema": 3, "time": timestamp if source == "backlog" else timestamp or datetime.now(timezone.utc).isoformat(),
            "source": source, "engine": engine, "model": model, "effort": effort, "features": features,
            "indicators": indicators, "propensity": propensity, "predicted": predicted,
            "rating": None, "tags": [], "label": combine(indicators)}


def events(db=None):
    if db is None:
        with connect() as db: return events(db)
    return [json.loads(r[0]) for r in db.execute("SELECT row FROM events ORDER BY rowid")]


def setting(db, key, default=None):
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_setting(db, key, value):
    db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value, allow_nan=False)))


def put(db, event):
    # Reconstruct the entire row: arbitrary input fields never reach storage.
    clean = make_event(event["features"], event["engine"], event["model"], event["effort"], event["indicators"], source=event["source"], timestamp=event["time"], propensity=event.get("propensity"), predicted=event.get("predicted"), override=event["indicators"].get("manual_override", False))
    clean["id"] = uuid.UUID(event["id"]).hex
    if event.get("rating") not in (None, 0, 1): raise ValueError("invalid rating")
    clean["rating"] = event.get("rating")
    clean["tags"] = sorted(set(event.get("tags", [])))
    if any(t not in TAGS for t in clean["tags"]): raise ValueError("invalid tag")
    clean["label"] = combine(clean["indicators"], clean["rating"], clean["tags"])
    db.execute("INSERT INTO events VALUES (?,?) ON CONFLICT(id) DO UPDATE SET row=excluded.row", (clean["id"], json.dumps(clean, allow_nan=False)))
    return clean


def add(event):
    from .policy import rebuild
    with connect() as db:
        result = put(db, event)
        rebuild(db)
        return result


def revise(identity, *, rating=None, indicators=None):
    from .policy import rebuild
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT row FROM events WHERE id=?", (identity,)).fetchone()
        if not row: raise ValueError("unknown turn")
        event = json.loads(row[0])
        if rating is not None:
            preference = rating.get("preference")
            if preference is not None:
                if preference not in (0., .5, 1.): raise ValueError("invalid preference")
                if not db.execute("SELECT a FROM pairs WHERE a=?",(identity,)).fetchone(): raise ValueError("no shadow for this turn")
                db.execute("UPDATE pairs SET preference=? WHERE a=?",(preference,identity))
            event.update(rating)
        if indicators is not None:
            event["indicators"].update(clean_indicators(indicators))
        result = put(db, event)
        rebuild(db)
        return result


def add_pair(a, b, propensity):
    if type(propensity) not in (int,float) or not 0 < propensity <= 1: raise ValueError("invalid pair propensity")
    with connect() as db:
        for key in (a,b):
            if not db.execute("SELECT id FROM events WHERE id=?",(key,)).fetchone(): raise ValueError("unknown pair turn")
        if a == b: raise ValueError("identical pair")
        db.execute("INSERT INTO pairs VALUES (?,?,NULL,?)",(a,b,propensity))


def pairs(db):
    return [dict(zip(("a","b","preference","propensity"),r)) for r in db.execute("SELECT a,b,preference,propensity FROM pairs ORDER BY rowid")]
