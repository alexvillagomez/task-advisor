"""Local editable routing settings; no credentials or network access."""
import json
import math
from .storage import state_dir

DEFAULTS = {
    "usage_bias": "low", "collection_target": 300, "daily_spend_cap": 5., "daily_quota_cap": 20.,
    "quota_stop_pct": 95., "billing": "subscription", "lambda_lat": .00001,
    "quota_pct_per_token": .0001, "quota_dollar_per_pct": .05,
    "stakes": {"low": {"value": .01, "failure": .01},
               "medium": {"value": 1., "failure": 2.},
               "high": {"value": 10., "failure": 50.}},
}


def config():
    values = json.loads(json.dumps(DEFAULTS))
    path = state_dir()/"config.json"
    if path.exists():
        supplied = json.loads(path.read_text(encoding="utf-8"))
        if set(supplied) - set(values): raise ValueError("unknown config keys")
        for k, v in supplied.items():
            if k == "stakes":
                for stake, preset in v.items():
                    if stake not in values[k] or set(preset) != {"value", "failure"}: raise ValueError("invalid stakes preset")
                    values[k][stake] = preset
            else: values[k] = v
    if values["billing"] not in {"subscription", "api"}: raise ValueError("invalid billing")
    if values["usage_bias"] not in {"low", "neutral"}: raise ValueError("invalid usage bias")
    numbers = [v for k,v in values.items() if k not in {"billing", "stakes", "usage_bias"}]
    numbers += [v for p in values["stakes"].values() for v in p.values()]
    if any(type(v) not in (int,float) or not math.isfinite(v) or v < 0 for v in numbers): raise ValueError("invalid config number")
    if type(values["collection_target"]) is not int or values["collection_target"] < 1: raise ValueError("invalid collection target")
    return values
