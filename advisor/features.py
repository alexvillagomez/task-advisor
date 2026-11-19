"""The only prompt-derived fields allowed across the persistence boundary."""
from __future__ import annotations

import re
import math
import os
import subprocess
from pathlib import Path
from datetime import datetime
from collections import Counter

TIERS = ("cheap", "mid", "frontier")
CATEGORIES = ("code", "math", "research", "writing", "general")

# All v3 additions are nullable: migration cannot reconstruct discarded text.
INT_FIELDS = "chars words lines code_blocks inline_code file_paths urls numbers list_items question_marks constraints cluster_id turns_since_switch cumulative_tokens local_hour weekday dirty_files".split()
FLOAT_FIELDS = "seconds_since_prompt session_age".split()
BOOL_FIELDS = "imperative_start previous_reference correction_cue is_git has_tests has_ci weekend".split()
ENUM_FIELDS = {"language": ("en", "other"), "output_format": ("none", "json", "table", "code"),
               "cluster_backend": ("hash", "minilm"), "previous_effort": ("none", "low", "medium", "high", "xhigh", "max", "unknown"),
               "file_count_bucket": ("tiny", "small", "medium", "large"), "project_size_bucket": ("tiny", "small", "medium", "large")}
EXTENSIONS = (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".cs", ".cpp", ".c", ".rb", ".php", ".swift", ".kt", ".sql", ".sh", ".ps1", ".html", ".css", ".r", ".ipynb", "other")
EXTRA_KEYS = set(INT_FIELDS + FLOAT_FIELDS + BOOL_FIELDS) | set(ENUM_FIELDS) | {"previous_model", "previous_label", "top_extensions"}


def migrate(features):
    return {**{k: None for k in EXTRA_KEYS}, **features}


def workspace(cwd):
    root = Path(cwd or os.getcwd())
    sizes = count = 0
    languages = Counter()
    # Bounds keep routing responsive; buckets saturate at large. No names escape.
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", ".venv", "__pycache__"} and not Path(directory,d).is_symlink()]
        for name in files:
            p = Path(directory,name)
            if p.is_symlink(): continue
            try: sizes += p.stat().st_size
            except OSError: continue
            count += 1
            suffix = p.suffix.lower()
            languages[suffix if suffix in EXTENSIONS else "other"] += 1
        if count >= 10000: break
    is_git, dirty = any((p/".git").exists() for p in (root,*root.parents)), None
    try:
        check = subprocess.run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"], capture_output=True, timeout=2)
        is_git = is_git or check.returncode == 0 and check.stdout.strip() == b"true"
        if check.returncode == 0 and is_git:
            status = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "-z", "--untracked-files=normal"], capture_output=True, timeout=3)
            if status.returncode == 0:
                chunks = iter(status.stdout.split(b"\0")); dirty = 0
                for chunk in chunks:
                    if chunk:
                        dirty += 1
                        if b"R" in chunk[:2] or b"C" in chunk[:2]: next(chunks, None)
    except (OSError, subprocess.TimeoutExpired): pass
    bucket = lambda n, cuts: ("tiny", "small", "medium", "large")[sum(n >= x for x in cuts)]
    return dict(is_git=is_git, dirty_files=dirty, file_count_bucket=bucket(count,(20,200,2000)),
                project_size_bucket=bucket(sizes,(100000,10000000,1000000000)),
                top_extensions=[k for k,_ in languages.most_common(3)],
                has_tests=any((root/d).is_dir() for d in ("tests","test","__tests__")),
                has_ci=any((root/d).exists() for d in (".github/workflows",".gitlab-ci.yml","azure-pipelines.yml",".circleci")))


def featurize(text: str, stakes: str = "medium", turn_index: int = 0, *, context=None, cwd=None, now=None, local=False, fast=False) -> dict:
    from .core import advise, _signals
    advice = advise(text or " — ", stakes, persist=False)
    signals = _signals(text)
    math = bool(re.search(r"\b(calculate|math|equation|sum|multiply|probability)\b|\d\s*[+*/=]\s*\d", text, re.I))
    category = "code" if signals["code"] else "math" if math else "research" if signals["external"] else "writing" if re.search(r"\b(write|rewrite|summarize|draft|translate)\b", text, re.I) else "general"
    result = {
        "length_bucket": "short" if len(text) < 240 else "medium" if len(text) < 1400 else "long",
        "code": signals["code"], "math": math, "multistep": signals["multi_step"],
        "question": "?" in text, "category": category, "stakes": stakes,
        "base_predictions": {r["model_tier"]: r["predicted_pass_chance"] for r in advice["tiers"]},
        "turn_index": max(0, int(turn_index)),
    }
    from .clusters import cluster
    cluster_id, backend = cluster(text, fast=fast)
    words = re.findall(r"\b\w+\b", text)
    result.update(chars=len(text), words=len(words), lines=text.count("\n")+1,
                  code_blocks=len(re.findall(r"```",text))//2, inline_code=len(re.findall(r"(?<!`)`[^`\n]+`(?!`)",text)),
                  file_paths=len(re.findall(r"(?:[\w.-]+[/\\])+[\w.-]+|\b\w+\.(?:py|js|ts|json|md|txt|csv)\b",text)),
                  urls=len(re.findall(r"https?://\S+",text)), numbers=len(re.findall(r"\b\d+(?:\.\d+)?\b",text)),
                  list_items=len(re.findall(r"(?m)^\s*(?:[-*+]\s|\d+[.)]\s)",text)), question_marks=text.count("?"),
                  imperative_start=bool(re.match(r"\s*(?:please\s+)?(?:write|create|make|fix|build|read|show|explain|find|list|calculate|summarize|translate|compare|implement|run)\b",text,re.I)),
                  language="en" if not words or sum(w.isascii() for w in words)/len(words) >= .8 else "other",
                  constraints=len(re.findall(r"\b(?:must|exactly|only)\b",text,re.I)),
                  output_format=next((k for k in ("json","table","code") if re.search(r"\b"+k+r"\b",text,re.I)),"none"),
                  previous_reference=bool(re.search(r"\b(?:that|above|again)\b",text,re.I)),
                  correction_cue=bool(re.search(r"\b(?:wrong|incorrect|instead|correction|not what|try again)\b",text,re.I)),
                  cluster_id=cluster_id, cluster_backend=backend)
    result = migrate(result)
    if context: result.update({k:v for k,v in context.items() if k in EXTRA_KEYS})
    if local or now is not None:
        moment = now or datetime.now().astimezone()
        result.update(local_hour=moment.hour, weekday=moment.weekday(), weekend=moment.weekday() >= 5)
    if local:
        result.update(workspace(cwd))
    return validate(result)


def validate(features: dict) -> dict:
    keys = {"length_bucket", "code", "math", "multistep", "question", "category", "stakes", "base_predictions", "turn_index"}
    if set(features) != keys | EXTRA_KEYS or features["length_bucket"] not in {"short", "medium", "long"} or features["category"] not in CATEGORIES or features["stakes"] not in {"low", "medium", "high"}:
        raise ValueError("invalid feature schema")
    if any(type(features[k]) is not bool for k in ("code", "math", "multistep", "question")):
        raise ValueError("invalid feature flags")
    predictions = features["base_predictions"]
    if set(predictions) != set(TIERS) or any(type(p) not in (float, int) or not 0 <= p <= 1 for p in predictions.values()):
        raise ValueError("invalid predictions")
    if type(features["turn_index"]) is not int or features["turn_index"] < 0:
        raise ValueError("invalid turn index")
    for k in INT_FIELDS + FLOAT_FIELDS:
        v = features[k]
        if v is not None and (type(v) not in ((int,) if k in INT_FIELDS else (int,float)) or not math.isfinite(v) or v < 0): raise ValueError("invalid numeric feature")
    for k, bound in (("cluster_id",64),("local_hour",24),("weekday",7)):
        if features[k] is not None and features[k] >= bound: raise ValueError("invalid bounded feature")
    for k in BOOL_FIELDS:
        if features[k] is not None and type(features[k]) is not bool: raise ValueError("invalid flag")
    for k, values in ENUM_FIELDS.items():
        if features[k] is not None and features[k] not in values: raise ValueError("invalid enum")
    from .catalog import models
    if features["previous_model"] is not None and features["previous_model"] not in {m["id"] for m in models()} | {"unknown"}: raise ValueError("invalid previous model")
    if features["previous_label"] is not None and (type(features["previous_label"]) not in (int,float) or not 0 <= features["previous_label"] <= 1): raise ValueError("invalid previous label")
    ext = features["top_extensions"]
    if ext is not None and (not isinstance(ext,list) or len(ext)>3 or any(e not in EXTENSIONS for e in ext)): raise ValueError("invalid extensions")
    return features


def vector(f: dict) -> list[float]:
    return [1., {"short": 0., "medium": .5, "long": 1.}[f["length_bucket"]],
            *[float(f[k]) for k in ("code", "math", "multistep", "question")],
            *[float(f["category"] == c) for c in CATEGORIES],
            {"low": 0., "medium": .5, "high": 1.}[f["stakes"]], min(f["turn_index"], 20) / 20.]


DIM = 13


def rich_vector(f):
    """New heads use all collected task/context fields; v2 residual ABI stays 13."""
    from .catalog import models
    x = vector(f)
    for k in INT_FIELDS + FLOAT_FIELDS:
        if k == "cluster_id": continue
        value = f.get(k)
        x += [min(1., math.log1p(value or 0)/12), float(value is not None)]
    for k in BOOL_FIELDS: x += [float(bool(f.get(k))), float(f.get(k) is not None)]
    for k, values in ENUM_FIELDS.items(): x += [float(f.get(k)==v) for v in values]
    x += [float(f.get("cluster_id")==i) for i in range(64)]
    x += [float(f.get("previous_model")==m["id"]) for m in models()]
    x += [float(f.get("previous_label") or 0), float(f.get("previous_label") is not None)]
    x += [float(e in (f.get("top_extensions") or [])) for e in EXTENSIONS]
    return x
