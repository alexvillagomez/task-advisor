"""Explicit local-only aggregate exports and count-weighted team priors."""
import json
import math
from pathlib import Path
import random
from .catalog import options
from .features import DIM
from .policy import choose, learn, predict, rebuild
from .storage import connect, events, setting, set_setting, state_dir

VERSION = "advisor-weights-v2"


def validate(payload):
    if set(payload) != {"version", "deltas", "counts"} or payload["version"] != VERSION: raise ValueError("incompatible prior schema")
    keys = {o["key"] for o in options()}
    if set(payload["deltas"]) != set(payload["counts"]) or not set(payload["deltas"]) <= keys: raise ValueError("invalid prior action keys")
    for key, values in payload["deltas"].items():
        count = payload["counts"][key]
        if type(count) not in (float, int) or not math.isfinite(count) or count < 0: raise ValueError("invalid count")
        if not isinstance(values, list) or len(values) != DIM or any(type(v) not in (float, int) or not math.isfinite(v) or abs(v) > 4 for v in values): raise ValueError("invalid weights")
    return payload


def write(payload, path):
    validate(payload)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    return path


def export_update(path=None, noise=0.):
    if not math.isfinite(noise) or noise < 0: raise ValueError("noise must be a nonnegative Gaussian standard deviation")
    with connect() as db:
        weights, counts = setting(db, "personal", {}), setting(db, "counts", {})
    rng = random.SystemRandom()
    deltas = {}
    for key, values in weights.items():
        if not counts.get(key): continue
        norm = math.sqrt(sum(v*v for v in values))
        deltas[key] = [max(-4., min(4., v/max(1., norm) + rng.gauss(0, noise))) for v in values]
    return write({"version": VERSION, "deltas": deltas, "counts": {k: counts[k] for k in deltas}}, path or state_dir()/"update.json")


def merge_updates(directory, output=None):
    output = Path(output or state_dir()/"team-prior.json")
    payloads = []
    for path in sorted(Path(directory).glob("*.json")):
        if path.resolve() == output.resolve(): continue
        payloads.append(validate(json.loads(path.read_text(encoding="utf-8"))))
    if not payloads: raise ValueError("no update files")
    keys = set().union(*(set(p["deltas"]) for p in payloads))
    counts = {k: sum(p["counts"].get(k, 0) for p in payloads) for k in keys}
    deltas = {k: [sum(p["deltas"].get(k, [0.]*DIM)[i]*p["counts"].get(k, 0) for p in payloads)/counts[k] if counts[k] else 0. for i in range(DIM)] for k in keys}
    return write({"version": VERSION, "deltas": deltas, "counts": counts}, output)


def use_prior(path):
    payload = validate(json.loads(Path(path).read_text(encoding="utf-8")))
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        set_setting(db, "team", payload["deltas"])
        rebuild(db)


def evaluate(export=False):
    with connect() as db:
        rows, team = events(db), setting(db, "team", {})
        from .baseline import evaluate_later
        from .storage import pairs
        later = evaluate_later(rows,pairs(db),setting(db,"user_baseline"),team)
    result = {"evaluation": "prequential personal; fixed team/baseline; observed actions only, not counterfactual quality", "tiers": {}}
    available = options()
    for name, prior in (("baseline", {}), ("team", team), ("personal", team)):
        loss = weight = cost = 0.
        match = good = 0
        online, online_counts = prior, {}
        for row in rows:
            weights = online if name == "personal" else prior
            if name == "personal": online, online_counts = learn([row], online, online_counts)
            action = next((o for o in available if o["id"] == row["model"] and o["effort"] == row["effort"] and o["engine"] == row["engine"]), None)
            if action is None: continue
            p = max(1e-6, min(1-1e-6, predict(action, row["features"], weights)))
            y, w = row["label"]["reward"], row["label"]["confidence"] * (.25 if row["source"] == "backlog" else 1)
            loss -= w*(y*math.log(p)+(1-y)*math.log(1-p)); weight += w
            picked = choose(row["features"], row["engine"], exploration=0, weights=weights,baseline={},rows=[])
            if picked["key"] == action["key"] and row["rating"] is not None and not row["indicators"].get("manual_override"):
                match += 1; good += row["rating"]; cost += row["indicators"].get("cost_usd", 0)
        result["tiers"][name] = {"log_loss": loss/weight if weight else None, "label_weight": weight, "pick_rated_good_rate": good/match if match else None, "matched_rated_picks": match, "matched_spend_usd": cost}
    result["observed_total_spend_usd"] = sum(r["indicators"].get("cost_usd", 0) for r in rows)
    result["heldout"] = later
    if export:
        path = Path(export) if isinstance(export, str) else state_dir()/"eval.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
