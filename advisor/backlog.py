"""Read existing histories in place, discard content before committing any rows."""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid
import zipfile

from ingest.parsers import parse_source_file
from ingest.outcomes import apply_outcome_rules
from ingest.signals import classify_followup
from .catalog import known_model
from .features import featurize
from .storage import connect, events, make_event, put
from .policy import rebuild


def stamp(value):
    try:
        if isinstance(value, (float, int)): return datetime.fromtimestamp(value, timezone.utc).isoformat()
        if value:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError): pass
    return None


def historical_features(text, index, timestamp):
    moment = datetime.fromisoformat(timestamp).astimezone() if timestamp else None
    return featurize(text,turn_index=index,now=moment)


def chatgpt_rows(path):
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            name = next((n for n in archive.namelist() if n == "conversations.json" or n.endswith("/conversations.json")), None)
            if name is None: raise ValueError("export has no conversations.json")
            with archive.open(name) as handle: conversations = json.load(handle)
    else:
        with path.open(encoding="utf-8") as handle: conversations = json.load(handle)
    if isinstance(conversations, dict): conversations = [conversations]
    for conversation in conversations if isinstance(conversations, list) else []:
        if not isinstance(conversation, dict): continue
        mapping = conversation.get("mapping") or {}
        if not isinstance(mapping, dict): continue
        current = conversation.get("current_node")
        if current not in mapping:
            # Missing current_node: use latest leaf, never concatenate all branches.
            leaves = [k for k, n in mapping.items() if isinstance(n, dict) and not n.get("children")]
            current = max(leaves, key=lambda k: stamp((mapping[k].get("message") or {}).get("create_time")) or "", default=None)
        path_nodes, seen = [], set()
        while current in mapping and current not in seen:
            seen.add(current)
            node = mapping[current]
            if not isinstance(node, dict): break
            path_nodes.append({**node, "_node_key": current})
            current = node.get("parent")
        path_nodes.reverse()
        human = []
        def role(n): return ((n.get("message") or {}).get("author") or {}).get("role")
        def siblings(n, target):
            parent = n.get("parent")
            return sum(role(s) == target for s in mapping.values() if isinstance(s, dict) and s.get("parent") == parent) > 1
        for node in path_nodes:
            msg = node.get("message") or {}
            if role(node) == "user":
                parts = (msg.get("content") or {}).get("parts") or []
                text = "\n".join(p for p in parts if isinstance(p, str))
                human.append({"node": node["_node_key"], "text": text, "time": stamp(msg.get("create_time")), "edit": siblings(node, "user"), "regen": False, "model": None, "end": None})
            elif role(node) == "assistant" and human:
                human[-1].update(model=(msg.get("metadata") or {}).get("model_slug"), end=stamp(msg.get("create_time")))
                human[-1]["regen"] |= siblings(node, "assistant")
        for index, turn in enumerate(human):
            follow = classify_followup(turn["text"], human[index+1]["text"]) if index+1 < len(human) else {}
            correction = follow.get("classification") in {"correction", "complaint"}
            failure = correction or turn["regen"] or (index+1 < len(human) and human[index+1]["edit"])
            indicators = {"outcome_tier": "likely_failure" if failure else "unclear", "outcome_score": .2 if failure else .5, "regeneration": turn["regen"], "user_edit": turn["edit"], "correction": correction}
            if follow:
                indicators.update(retry_similarity=follow["retry_similarity"], retry=bool(follow["is_retry"]))
            if turn["time"] and turn["end"]:
                indicators["latency_ms"] = max(0, (datetime.fromisoformat(turn["end"])-datetime.fromisoformat(turn["time"])).total_seconds()*1000)
            row = make_event(historical_features(turn["text"],index,turn["time"]), "chatgpt", known_model(turn["model"], "codex"), "unknown", indicators, source="backlog", timestamp=turn["time"])
            row["id"] = uuid.uuid5(uuid.NAMESPACE_URL, "chatgpt:" + str(conversation.get("id") or conversation.get("conversation_id") or "") + ":" + str(turn["node"])).hex
            yield row


def engine_rows(path, source, digest):
    # Ingest excludes a supplied workspace. Use a nonexistent sentinel so personal
    # history from the current repository is included without changing ingest.
    sentinel = Path.home() / ".advisor-no-workspace-exclusion-8bd30a5d"
    episodes, diagnostics = parse_source_file(path, source, digest, sentinel)
    if diagnostics.get("parse_errors"):
        # Do not mark a partially written active transcript as fully imported.
        raise ValueError("incomplete or malformed history file")
    for ep in episodes:
        tier, score, _ = apply_outcome_rules(ep)
        follow, execution, tokens = ep["followup"], ep["execution"], ep["tokens"]
        indicators = {"outcome_tier": tier, "outcome_score": score, "retry": bool(follow["is_retry"]), "correction": follow["classification"] in {"correction", "complaint"}, "tool_failures": ep["tools"]["error_count"], "command_failures": ep["tools"]["nonzero_exit_count"], "tests_passed": sum(c["result"] == "pass" for c in execution["checks"]), "tests_failed": sum(c["result"] == "fail" for c in execution["checks"]), "input_tokens": tokens["input"], "output_tokens": tokens["output"], "cached_tokens": tokens["cached_input"]}
        if follow.get("retry_similarity") is not None: indicators["retry_similarity"] = follow["retry_similarity"]
        if ep["timing"]["agent_wall_time_ms"] is not None: indicators["latency_ms"] = ep["timing"]["agent_wall_time_ms"]
        row = make_event(historical_features(ep["prompt_text"],ep["turn_index"],stamp(ep["request_at"])), source, known_model(ep["model"], source), ep["reasoning_effort"], indicators, source="backlog", timestamp=ep["request_at"])
        row["id"] = uuid.uuid5(uuid.NAMESPACE_URL, source + ":" + ep["episode_id"]).hex
        yield row


def report(rows):
    rows = [r for r in rows if r["source"] == "backlog"]
    count = Counter((r["engine"], r["model"], r["indicators"].get("outcome_tier", "unclear")) for r in rows)
    return {"turns": len(rows), "counts": [{"source": s, "model": m, "label_tier": t, "count": n} for (s, m, t), n in sorted(count.items())], "unclear_share": sum(r["indicators"].get("outcome_tier") == "unclear" for r in rows)/len(rows) if rows else 0.}


def import_backlog(*, codex=False, claude=False, chatgpt_export=None, since=None, dry_run=False, home=None):
    home = Path(home or Path.home())
    sources = []
    for source, roots in (("codex", [home/".codex/sessions", home/".codex/archived_sessions"] if codex else []), ("claude", [home/".claude/projects"] if claude else [])):
        for root in roots:
            sources.extend((p, source) for p in root.rglob("*") if p.is_file() and (p.suffix == ".jsonl" or p.name.endswith(".jsonl.zst") or p.suffix == ".zst"))
    if chatgpt_export: sources.append((Path(chatgpt_export), "chatgpt"))
    threshold = stamp(since) if since else None
    if since and not threshold: raise ValueError("--since must be an ISO date")
    # A dry run never creates the data directory or database.
    from .storage import state_dir
    manifest, previous = {}, []
    if (state_dir()/"advisor.sqlite3").exists():
        with connect() as db:
            manifest = {k: json.loads(v) for k, v in db.execute("SELECT identity,value FROM manifest")}
            previous = events(db)
    prior_by_id = {r["id"]: r for r in previous}
    staged, changes, failed, skipped = [], {}, 0, 0
    seen_digests = set()
    for path, source in sorted(sources):
        try:
            digestor = hashlib.sha256()
            with path.open("rb") as handle:
                while chunk := handle.read(1024*1024): digestor.update(chunk)
            digest = digestor.hexdigest()
            identity = hashlib.sha256((source + str(path.resolve())).encode()).hexdigest()
            mtime = path.stat().st_mtime_ns
            old = manifest.get(identity, {})
            if digest in seen_digests or (old.get("hash") == digest and old.get("mtime") == mtime and old.get("since") == threshold):
                skipped += 1; continue
            seen_digests.add(digest)
            rows = list(chatgpt_rows(path) if source == "chatgpt" else engine_rows(path, source, digest))
            ids = []
            for row in rows:
                if threshold and (row["time"] is None or row["time"] < threshold): continue
                ids.append(row["id"])
                if row["id"] in prior_by_id:
                    row.update({k: prior_by_id[row["id"]][k] for k in ("rating", "tags")})
                staged.append(row)
            changes[identity] = {"hash": digest, "mtime": mtime, "since": threshold, "ids": ids}
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile): failed += 1
    if not dry_run and changes:
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in staged: put(db, row)
            for key, value in changes.items():
                # Keep earlier imported rows outside a new --since window.
                value["ids"] = sorted(set(value["ids"]) | set(manifest.get(key, {}).get("ids", [])))
                db.execute("INSERT OR REPLACE INTO manifest VALUES (?,?)", (key, json.dumps(value)))
            rebuild(db)
    return {**report(staged), "processed_files": len(changes), "skipped_files": skipped, "failed_files": failed, "dry_run": dry_run}
