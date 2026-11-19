"""IPS pass fitting, Bradley-Terry pairs, later-turn evaluation, verification head."""
import json
import math
from datetime import datetime
from .catalog import options
from .features import rich_vector
from .policy import sigmoid, predict, learn
from .storage import connect, events, pairs, setting, set_setting, state_dir


def action(row):
    return next((o for o in options() if o["engine"]==row["engine"] and o["id"]==row["model"] and o["effort"]==row["effort"]),None)


def logit(p):
    p = max(.001,min(.999,p))
    return math.log(p/(1-p))


def score(option, f, weights):
    return logit(predict(option,f,{})) + sum(a*b for a,b in zip(weights.get(option["key"],[]),rich_vector(f)))


def probability(option, f, weights):
    return sigmoid(score(option,f,weights))


def fit(rows, comparisons=(), epochs=12):
    weights, counts = {}, {}
    eligible = [r for r in rows if r["source"]=="live" and r["indicators"].get("collection") and not r["indicators"].get("manual_override") and r.get("propensity") and r["rating"] is not None and action(r)]
    by_id = {r["id"]:r for r in rows}
    comparisons = [p for p in comparisons if p["preference"] is not None and p["a"] in by_id and p["b"] in by_id and action(by_id[p["a"]]) and action(by_id[p["b"]]) and not by_id[p["a"]]["indicators"].get("manual_override")]
    # Exact IPS ratios, normalized by the sample mean to keep SGD step sizes stable.
    mean_ips = sum(1/r["propensity"] for r in eligible)/len(eligible) if eligible else 1.
    mean_pair = sum(1/p["propensity"] for p in comparisons)/len(comparisons) if comparisons else 1.
    def update(o,x,error,weight,rate):
        w = weights.setdefault(o["key"],[0.]*len(x))
        weights[o["key"]] = [max(-4.,min(4.,v+rate*(weight*error*z-.002*v))) for v,z in zip(w,x)]
    for epoch in range(epochs):
        rate = .05/math.sqrt(epoch+1)
        for r in eligible:
            o = action(r); x = rich_vector(r["features"])
            weight = 1/r["propensity"]/mean_ips
            update(o,x,r["rating"]-probability(o,r["features"],weights),weight,rate)
            if epoch==0: counts[o["key"]] = counts.get(o["key"],0)+1/r["propensity"]
        for p in comparisons:
            a,b = by_id[p["a"]],by_id[p["b"]]
            oa,ob = action(a),action(b)
            # P(A > B) = sigmoid(s_A - s_B); ties are target 1/2.
            error = p["preference"]-sigmoid(score(oa,a["features"],weights)-score(ob,b["features"],weights))
            weight = 1/p["propensity"]/mean_pair
            update(oa,rich_vector(a["features"]),error,weight,rate*.5)
            update(ob,rich_vector(b["features"]),-error,weight,rate*.5)
    return {"weights":weights,"counts":counts,"rated_examples":len(eligible),"pair_examples":len(comparisons)}


def train_baseline(fraction=.8):
    if not 0 < fraction <= 1: raise ValueError("training fraction must be in (0,1]")
    with connect() as db:
        rows, comparisons = events(db), pairs(db)
        ordered = sorted((r for r in rows if r["time"] and r["source"]=="live" and not r["indicators"].get("shadow")), key=lambda r:r["time"])
        if not ordered: raise ValueError("collect and rate turns before training")
        n = max(1,int(len(ordered)*fraction))
        cutoff = ordered[n-1]["time"]
        # Include B paired with A at the split even if B completed later.
        train_ids = {r["id"] for r in rows if r["time"] and r["time"]<=cutoff}
        for p in comparisons:
            if p["a"] in train_ids: train_ids.add(p["b"])
        training = [r for r in rows if r["id"] in train_ids]
        fitted = fit(training,comparisons)
        if not fitted["rated_examples"] and not fitted["pair_examples"]: raise ValueError("no rated collection turns in training partition")
        payload = {"version":"advisor-baseline-v3", "cutoff":cutoff,"training_ids":sorted(train_ids),**fitted}
        set_setting(db,"user_baseline",payload)
        from .policy import rebuild
        rebuild(db)
    path = state_dir()/"user-baseline.json"
    path.write_text(json.dumps(payload,allow_nan=False,indent=2),encoding="utf-8")
    return path


def verification_target(row, losses=()):
    if "v" in row["tags"] or "w" in row["tags"] or row["indicators"].get("tests_failed") or row["id"] in losses: return 1.
    if row["rating"] is not None or row["indicators"].get("tests_passed"): return 0.
    return None


def fit_verification(rows, comparisons=()):
    losses = {p["b"] if p["preference"]==1 else p["a"] for p in comparisons if p["preference"] in (0.,1.)}
    weights, count = {}, 0
    for r in rows:
        y = verification_target(r,losses); o = action(r)
        if y is None or o is None: continue
        x = rich_vector(r["features"]); key = o["key"]
        # Shared contextual vector plus an action-specific bias generalizes sparse actions.
        w = weights.setdefault("shared",[0.]*len(x))
        bias = weights.get(key,0.)
        error = y-sigmoid(bias+sum(a*b for a,b in zip(w,x)))
        rate = .1/math.sqrt(1+count*.02)
        weights["shared"] = [max(-4.,min(4.,v+rate*(error*z-.002*v))) for v,z in zip(w,x)]
        weights[key] = max(-4.,min(4.,bias+rate*error)); count += 1
    return {"weights":weights,"count":count}


def verification(option, f, head):
    if head.get("count",0) >= 30:
        w = head["weights"]
        p = sigmoid(w.get(option["key"],0.)+sum(a*b for a,b in zip(w.get("shared",[]),rich_vector(f))))
        advice = "human review" if f["stakes"]=="high" else "executable test" if f["code"] else "cross-model check" if p>=.5 else "self-check" if p>=.25 else "none"
        return p,advice
    from .core import _verification
    return None,_verification(f["stakes"],option["predicted"],{"code":f["code"],"high_risk":f["stakes"]=="high"})


def metrics(observations):
    if not observations: return {k:None for k in ("log_loss","brier","ece","rated_good_rate","cost_per_success") } | {"rated_turns":0,"spend_usd":0.}
    n = len(observations); loss = brier = ece = 0.
    for p,y,c in observations:
        p=max(1e-6,min(1-1e-6,p)); loss -= y*math.log(p)+(1-y)*math.log(1-p); brier += (p-y)**2
    for i in range(10):
        bucket = [(p,y) for p,y,_ in observations if min(9,int(p*10))==i]
        if bucket: ece += abs(sum(p-y for p,y in bucket))/n
    successes = sum(y for _,y,_ in observations); cost = sum(c for _,_,c in observations)
    return dict(log_loss=loss/n,brier=brier/n,ece=ece,rated_good_rate=successes/n,rated_turns=n,spend_usd=cost,cost_per_success=cost/successes if successes else None)


def evaluate_later(rows, comparisons, artifact, team):
    if not artifact: return {"status":"Run advisor train-baseline to reserve later turns for evaluation"}
    from .policy import choose
    train_ids = set(artifact["training_ids"])
    training = [r for r in rows if r["id"] in train_ids]
    heldout = sorted((r for r in rows if r["id"] not in train_ids and r["source"]=="live" and r["time"] and r["time"]>artifact["cutoff"]),key=lambda r:r["time"])
    personal, counts = learn(training,team,baseline=artifact["weights"])
    observations = {k:[] for k in ("shipped_baseline","user_baseline","personal")}
    ope = {k:[] for k in observations}
    previous = list(training)
    for r in heldout:
        o = action(r)
        for name in observations:
            w = personal if name=="personal" else {}
            base = artifact["weights"] if name in {"user_baseline","personal"} else {}
            if o is None or r["rating"] is None: continue
            p = predict(o,r["features"],w,base)
            observations[name].append((p,r["rating"],r["indicators"].get("cost_usd",0)))
            if r["indicators"].get("collection") and not r["indicators"].get("manual_override") and not r["indicators"].get("shadow") and r.get("propensity"):
                picked = choose(r["features"],r["engine"],exploration=0,weights=w,baseline=base,rows=previous,now=datetime.fromisoformat(r["time"]))
                support = r["indicators"].get("selection_probabilities",{})
                if not support.get(picked["key"]): continue
                matched = float(picked["key"]==o["key"]); ips = matched/r["propensity"]
                # DR = m(x,pi) + I(a=pi)/q(a|x) * (y-m(x,a)).
                ope[name].append((ips*r["rating"], picked["predicted"]+ips*(r["rating"]-p), ips*r["indicators"].get("cost_usd",0),matched,r["rating"]))
        personal,counts = learn([r],personal,counts,baseline=artifact["weights"]); previous.append(r)
    result = {"cutoff":artifact["cutoff"],"heldout_turns":len(heldout),"method":"later turns; user baseline frozen; personal prequential; exact IPS and DR for rated randomized primary turns only"}
    for name,obs in observations.items():
        result[name] = metrics(obs)
        values=ope[name]; n=len(values); matched=sum(v[3] for v in values)
        result[name]["off_policy"] = {"n":n,"ips_good":sum(v[0] for v in values)/n if n else None,"dr_good":sum(v[1] for v in values)/n if n else None,"ips_spend_usd":sum(v[2] for v in values)/n if n else None,"matched_picks":matched,"pick_rated_good_rate":sum(v[3]*v[4] for v in values)/matched if matched else None}
    return result
