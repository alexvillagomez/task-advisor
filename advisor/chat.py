"""Terminal router and quick ratings."""
from ingest.signals import classify_followup
from .catalog import models
from .features import featurize
from .labels import parse_rating
from .policy import choose
from .storage import add, make_event, revise, events, connect, setting, pairs, add_pair
import time
from .engines import run


def rating_line(input_fn=input, output=print, pair=False, answer_b=None):
    while True:
        try:
            value = input_fn("Rate [+ good / - bad / Enter skip; tags v o u w p s c" + ("; a/b/= preference, /b expands B" if pair else "") + "]: ")
            if value.strip()=="/b" and pair:
                output(answer_b or "No B answer."); continue
            rating = parse_rating(value)
            if "preference" in rating and not pair: raise ValueError("Pairwise rating requires a shadow answer")
            return rating
        except ValueError as exc: output(str(exc))
        except (EOFError, KeyboardInterrupt): return parse_rating("")


def chat(engine, stakes="medium", cwd=None, exploration=0, mode="route", collect_target=None, shadow=False, shadow_high=False, objective="threshold", *, input_fn=input, output=print, executable=None):
    output("Advisor stores features/outcomes only. The engine sends prompts to its provider and may save transcripts in its own history. /quit exits; /model auto restores routing.")
    from .config import config
    from .baseline import verification
    cfg = config()
    if collect_target is not None:
        if collect_target < 1: raise ValueError("collection target must be positive")
        cfg["collection_target"] = collect_target
    session = previous = last_id = None
    previous_model = previous_effort = previous_label = previous_time = None
    cumulative_tokens = turns_since_switch = 0
    started = time.monotonic()
    last_b = None
    last_pair = False
    override, why, turn = None, "No turn yet.", 0
    while True:
        try: prompt = input_fn("You> ").strip()
        except (EOFError, KeyboardInterrupt): break
        if not prompt: continue
        if prompt.startswith("/"):
            cmd, _, value = prompt.partition(" ")
            if cmd == "/quit": break
            if cmd == "/why": output(why)
            elif cmd == "/stakes":
                if value in {"low", "medium", "high"}: stakes = value
                else: output("Use /stakes low|medium|high")
            elif cmd == "/model":
                if value == "auto": override = None
                elif value in {m["id"] for m in models() if m["engine"] == engine}: override = value
                else: output("Use /model auto or a catalog model ID for this engine")
            elif cmd == "/rate":
                if last_id: previous_label = revise(last_id, rating=rating_line(input_fn, output, last_pair, last_b))["label"]["reward"]
                else: output("No turn to rate yet.")
            elif cmd != "/why": output("Commands: /model X, /stakes low|medium|high, /why, /rate, /quit")
            continue
        if previous is not None and last_id:
            followup = classify_followup(previous, prompt)
            previous_label = revise(last_id, indicators={"retry_similarity": followup["retry_similarity"], "retry": bool(followup["is_retry"]), "correction": followup["classification"] in {"correction", "complaint"}})["label"]["reward"]
        now = time.monotonic()
        features = featurize(prompt, stakes, turn, cwd=cwd, local=True, context={
            "turns_since_switch":turns_since_switch, "seconds_since_prompt":now-previous_time if previous_time is not None else None,
            "previous_model":previous_model, "previous_effort":previous_effort,"previous_label":previous_label,
            "cumulative_tokens":cumulative_tokens,"session_age":now-started})
        rows = events()
        with connect() as db:
            preferred = {p["a"] for p in pairs(db) if p["preference"] is not None}
            head = setting(db,"verification",{})
        collected = sum(r["source"]=="live" and not r["indicators"].get("shadow") and (r["rating"] is not None or r["id"] in preferred) for r in rows)
        active_mode = "collect" if mode=="auto" and collected < cfg["collection_target"] else "route" if mode=="auto" else mode
        output(f"collected {collected}/{cfg['collection_target']}; {active_mode} mode")
        option = choose(features, engine, exploration=exploration, override=override, mode=active_mode, objective=objective, rows=rows, cfg=cfg)
        why = f"{option['why']}; {features['category']}, {features['length_bucket']}; target {option['target']:.0%}; baseline {features['base_predictions'][option['tier']]:.0%}; propensity {option['propensity']:.4f}"
        windows = option["account"].get("windows",{})
        quota_text = "; ".join(f"{name} {100-w.get('quota_used_pct',0):.1f}% left resets {w.get('minutes_to_reset',0):.0f}m" for name,w in windows.items()) or "quota left/reset unknown"
        output(f"-> {option['id']} / {option['effort']}, P(pass) {option['predicted']:.0%}, ~${option['cost']:.6f} {'API' if cfg['billing']=='api' else 'quota value'}, ~{option['quota_impact']:.3f}% quota; {quota_text}; {why}")
        pv, advice = verification(option,features,head)
        output(f"Verify: {advice}" + (f" (P(needs verification) {pv:.0%})" if pv is not None else " (rules; fewer than 30 labels)"))
        try: answer, returned, indicators = run(engine, option, prompt, session, cwd, executable)
        except ValueError as exc:
            output(str(exc)); continue
        session = returned
        output(answer or "Engine returned no answer. Check CLI authentication/model access and permissions.")
        indicators.update(collection=option["collection"], quota_impact=option["quota_impact"],quota_estimated=option["quota_estimated"])
        if option.get("distribution"): indicators["selection_probabilities"] = option["distribution"]
        event = add(make_event(features, engine, option["id"], option["effort"], indicators, propensity=option["propensity"], predicted=option["predicted"], override=option["override"]))
        last_id = event["id"]
        last_b, last_pair = None, False
        if shadow and (stakes!="high" or shadow_high):
            current_rows = events()
            b_features = {**features,"turn_index":0,"turns_since_switch":0,"seconds_since_prompt":None,
                          "previous_model":None,"previous_effort":None,"previous_label":None,"cumulative_tokens":0,"session_age":0.}
            shadow_option = choose(b_features,engine,mode="collect",weights={},override=None,exclude_model=option["id"],rows=current_rows,cfg=cfg)
            if not shadow_option["paused"]:
                # The installed CLIs resume mutable threads, not a pre-A snapshot.
                # Fresh B avoids leaking A's current answer into the comparison.
                try:
                    last_b, _, b_ind = run(engine,shadow_option,prompt,None,cwd,executable,readonly=True)
                    b_ind.update(shadow=True,collection=True,quota_impact=shadow_option["quota_impact"],quota_estimated=shadow_option["quota_estimated"])
                    b_ind["selection_probabilities"] = shadow_option["distribution"]
                    b = add(make_event(b_features,engine,shadow_option["id"],shadow_option["effort"],b_ind,propensity=shadow_option["propensity"],predicted=shadow_option["predicted"]))
                    add_pair(last_id,b["id"],option["propensity"]*shadow_option["propensity"])
                    last_pair = True
                    output(f"B [collapsed; /b to expand]: {shadow_option['id']} / {shadow_option['effort']}; fresh read-only context; {b_ind.get('latency_ms',0)}ms; ~${b_ind.get('cost_usd',0):.6f}")
                except ValueError as exc: output(str(exc))
            else: output("Shadow paused by daily spend/quota cap.")
        elif shadow: output("Shadow skipped for high stakes; --shadow-high enables it.")
        rated = revise(last_id, rating=rating_line(input_fn, output, last_pair, last_b))
        previous_label = rated["label"]["reward"]
        turns_since_switch = turns_since_switch+1 if previous_model==option["id"] else 1
        previous_model, previous_effort, previous_time = option["id"],option["effort"],now
        cumulative_tokens += int(indicators.get("input_tokens",0)+indicators.get("output_tokens",0))
        previous = prompt
        turn += 1
        if not session:
            output("Engine supplied no session ID; stopping to avoid silently losing conversation context.")
            break
    return 0
