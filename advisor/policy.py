"""Per-user contextual logistic bandit over model/effort actions."""
from __future__ import annotations

import math
import random

from .catalog import estimate, options
from .features import DIM, TIERS, vector
from .storage import connect, events, setting, set_setting

TARGETS = {"low": .72, "medium": .88, "high": .95}


def sigmoid(x):
    return 1 / (1 + math.exp(-max(-24, min(24, x))))


def predict(option, features, weights, baseline=None):
    p = max(.001, min(.999, features["base_predictions"][option["tier"]]))
    # Effort offsets are an explicit cold-start estimate, not measured performance.
    effort = {"none": -.08, "low": 0, "medium": .12, "high": .24, "xhigh": .3, "max": .35}[option["effort"]]
    from .features import rich_vector
    offset = sum(a*b for a,b in zip((baseline or {}).get(option["key"],[]),rich_vector(features))) if baseline else 0.
    return sigmoid(math.log(p / (1-p)) + effort + offset + sum(a*b for a, b in zip(weights.get(option["key"], [0.]*DIM), vector(features))))


def choose(features, engine, *, exploration=0, rng=None, override=None, weights=None, baseline=None,
           mode="route", objective="threshold", rows=None, cfg=None, exclude=None, exclude_model=None, now=None):
    if not 0 <= exploration <= 1: raise ValueError("exploration must be between 0 and 1")
    if weights is None:
        with connect() as db: weights = setting(db, "personal", setting(db, "team", {}))
    if baseline is None:
        with connect() as db: baseline = setting(db,"user_baseline",{}).get("weights",{})
    from .config import config
    from .quota import price, budget
    cfg = cfg or config()
    rows = events() if rows is None else rows
    budget_state = budget(rows,cfg,engine,now)
    candidates = options(engine)
    if exclude: candidates = [o for o in candidates if o["key"]!=exclude]
    if exclude_model: candidates = [o for o in candidates if o["id"]!=exclude_model]
    ranked = [{**o, "predicted": predict(o, features, weights, baseline), **price(o,features,rows,cfg,budget_state["account"])} for o in candidates]
    if objective == "threshold":
        for o in ranked: o["cost"] = estimate(o,features)
    target = TARGETS[features["stakes"]]
    eligible = [o for o in ranked if o["predicted"] >= target]
    low = cfg.get("usage_bias", "low") == "low"
    effort_order = {e:i for i,e in enumerate(("none","low","medium","high","xhigh","max"))}
    penalty = cfg["lambda_lat"] if low else 0.
    def tie(o):
        return (effort_order[o["effort"]] if low else 0, -o["predicted"])
    greedy = min(eligible, key=lambda o: (o["cost"] + penalty*o["latency"], *tie(o))) if eligible else max(ranked, key=lambda o: (o["predicted"], -o["cost"]))
    if objective == "ev":
        preset = cfg["stakes"][features["stakes"]]
        for o in ranked:
            o["ev"] = preset["value"]*o["predicted"]-preset["failure"]*(1-o["predicted"])-o["cost"]-cfg["lambda_lat"]*o["latency"]
        greedy = max((o for o in ranked if features["stakes"]!="high" or o["tier"]!="cheap"),key=lambda o:(o["ev"], -effort_order[o["effort"]] if low else 0))
    if mode == "collect" and not override:
        pool = [o for o in ranked if (features["stakes"]!="high" or o["tier"]!="cheap") and o["api_cost"]<=budget_state["spend_left"] and o["quota_impact"]<=budget_state["quota_left"]]
        if pool and not budget_state["paused"]:
            distribution = collection_distribution(pool,rows,features["category"])
            rng = rng or random.SystemRandom()
            draw, cumulative, chosen = rng.random(),0.,pool[-1]
            for o in pool:
                cumulative += distribution[o["key"]]
                if draw < cumulative: chosen=o; break
            return {**chosen,"propensity":distribution[chosen["key"]],"distribution":distribution,"override":False,"why":"stratified collection","target":target,"collection":True,"paused":False}
        exploration = 0
    # Exclude all tiers below the first baseline tier at >=50%; if none qualifies,
    # only frontier exploration is possible. Personal optimism cannot relax this.
    floor = next((i for i, t in enumerate(TIERS) if features["base_predictions"][t] >= .5), 2)
    pool = [o for o in ranked if TIERS.index(o["tier"]) >= floor]
    # Explore near the greedy pick only: never jump to options far costlier than it (manager fix 2026-11-19).
    cap = max(greedy["cost"] * 4, 0.002)
    pool = [o for o in pool if o["cost"] <= cap] or pool
    if objective == "ev":
        pool = [o for o in pool if o["api_cost"] <= budget_state["spend_left"] and o["quota_impact"] <= budget_state["quota_left"]]
    epsilon = 0. if features["stakes"] == "high" or budget_state["paused"] else exploration
    if not pool: pool,epsilon = [greedy],0.
    rng = rng or random.SystemRandom()
    chosen = rng.choice(pool) if epsilon and rng.random() < epsilon else greedy
    propensity = (1-epsilon if chosen["key"] == greedy["key"] else 0.) + (epsilon / len(pool) if any(o["key"] == chosen["key"] for o in pool) else 0.)
    if override:
        chosen = next((o for o in ranked if o["id"] == override and o["effort"] == greedy["effort"]), None) or next((o for o in ranked if o["id"] == override), None)
        if chosen is None: raise ValueError("model must be an ID in advisor/models.json for this engine")
        propensity = 1.  # Conditional on the user's forced action; excluded from policy evaluation.
    why = "manual override" if override else "cheapest above target" if chosen["key"] == greedy["key"] and eligible else "best predicted; target unmet" if chosen["key"] == greedy["key"] else "safe exploration"
    if objective=="ev" and not override: why = "maximum expected value" if chosen["key"]==greedy["key"] else "safe exploration"
    if mode=="collect" and not override: why += "; collection paused by daily/quota cap"
    return {**chosen, "propensity": propensity, "override": bool(override), "why": why, "target": target,"collection":False,"paused":mode=="collect" and not override}


def collection_distribution(pool, rows, category):
    """q(a|x) proportional to 1/sqrt((1+n_a)*(1+n_a,category))."""
    weights = {}
    for o in pool:
        matching = [r for r in rows if r["engine"]==o["engine"] and r["model"]==o["id"] and r["effort"]==o["effort"] and r["source"]=="live"]
        count = len(matching); categorized = sum(r["features"]["category"]==category for r in matching)
        weights[o["key"]] = 1/math.sqrt((1+count)*(1+categorized))
    total = sum(weights.values())
    return {k:w/total for k,w in weights.items()}


def learn(rows, prior, initial_counts=None, baseline=None):
    weights = {k: list(v) for k, v in prior.items()}
    counts = dict(initial_counts or {})
    all_options = options()
    for row in rows:
        matches = [o for o in all_options if o["id"] == row["model"] and o["engine"] == ("codex" if row["engine"] == "chatgpt" else row["engine"]) and (row["effort"] == "unknown" or o["effort"] == row["effort"])]
        if not matches: continue
        option = matches[0]
        label = row["label"]
        strength = label["confidence"] * (.25 if row["source"] == "backlog" else 1.)
        updates = [(o, label["reward"], strength/len(matches)) for o in matches]
        # Counterfactual tag hints are deliberately weaker than observed ratings.
        for o in all_options:
            if o["engine"] != option["engine"]: continue
            if "o" in row["tags"] and TIERS.index(o["tier"]) < TIERS.index(option["tier"]): updates.append((o, .95, strength*.35))
            if "u" in row["tags"]:
                updates.append((o, .95 if TIERS.index(o["tier"]) > TIERS.index(option["tier"]) else .05, strength*.35))
        x = vector(row["features"])
        for o, target, weight in updates:
            key = o["key"]
            error = target - predict(o, row["features"], weights, baseline)
            w = weights.setdefault(key, [0.]*DIM)
            rate = .25 / math.sqrt(1 + counts.get(key, 0)*.02)
            weights[key] = [max(-4., min(4., value + rate * weight * (error * feature - .002 * value))) for value, feature in zip(w, x)]
            counts[key] = counts.get(key, 0) + weight
    return weights, counts


def rebuild(db):
    rows = events(db)
    weights, counts = learn(rows, setting(db, "team", {}), baseline=setting(db,"user_baseline",{}).get("weights",{}))
    set_setting(db, "personal", weights)
    set_setting(db, "counts", counts)
    from .baseline import fit_verification
    from .storage import pairs
    set_setting(db,"verification",fit_verification(rows,pairs(db)))
