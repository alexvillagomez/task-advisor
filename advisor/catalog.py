"""Editable local price/capability registry; no remote lookups."""
import json
from pathlib import Path
from functools import lru_cache


@lru_cache(maxsize=1)
def models():
    return json.loads(Path(__file__).with_name("models.json").read_text(encoding="utf-8"))["models"]


def options(engine=None):
    return [{**m, "effort": e, "key": m["engine"] + ":" + m["id"] + ":" + e}
            for m in models() if engine is None or m["engine"] == engine for e in m["efforts"]]


def known_model(value, engine):
    for m in models():
        if m["engine"] == engine and value == m["id"]:
            return m["id"]
    # Historical IDs are reduced to a known family, never stored as arbitrary text.
    value = str(value or "").lower()
    families = (("luna", "gpt-6-luna"), ("terra", "gpt-6.1-terra"), ("sol", "gpt-6.1-sol"), ("astra", "gpt-6-astra")) if engine == "codex" else (("haiku", "haiku"), ("sonnet", "sonnet"), ("opus", "opus"))
    return next((m for family, m in families if family in value), "unknown")


def estimate(option, f):
    tokens = {"short": 60, "medium": 350, "long": 1500}[f["length_bucket"]]
    output = {"low": 300, "medium": 600, "high": 1200, "xhigh": 1800, "max": 2400}.get(option["effort"], 300)
    return (tokens * option["input"] + output * option["output"]) / 1e6
