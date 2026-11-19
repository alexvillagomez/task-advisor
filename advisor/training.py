"""Train the advisor's small, dependency-free text classifier.

The artifact is a sparse bag-of-words logistic model per observed model
configuration.  Runtime heuristics intentionally live in core.py instead of
being smuggled into this learned component.
"""

from __future__ import annotations

import csv
import json
import math
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRIALS = ROOT / "model" / "out" / "trials.csv"
DEFAULT_TIERS = Path(__file__).with_name("tiers.json")
DEFAULT_ARTIFACT = Path(__file__).with_name("artifacts") / "predictor.json"
TOKEN_RE = re.compile(r"[a-z][a-z0-9_+-]{1,}|\d+(?:\.\d+)?", re.I)


def text_features(text: str) -> list[str]:
    """Return stable unigram/bigram features; numbers share one token."""

    words = ["<number>" if token[0].isdigit() else token.lower() for token in TOKEN_RE.findall(text)]
    unigrams = [f"u:{word}" for word in words]
    bigrams = [f"b:{left}_{right}" for left, right in zip(words, words[1:])]
    return list(dict.fromkeys(unigrams + bigrams))


def _sigmoid(value: float) -> float:
    value = max(-24.0, min(24.0, value))
    return 1.0 / (1.0 + math.exp(-value))


def _load_tier_configs(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(item["learned_configuration"]) for item in payload["tiers"].values()}


def _iter_rows(path: Path, configurations: set[str]) -> Iterable[dict[str, object]]:
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("configuration") not in configurations or row.get("passed") not in {"True", "False"}:
                continue
            prompt = row.get("prompt", "")
            yield {
                "configuration": row["configuration"],
                "passed": row["passed"] == "True",
                "prompt": prompt,
                "family": row.get("family", "unknown"),
                "words": len(prompt.split()),
                "lines": prompt.count("\n") + 1,
                "output_tokens": float(row["output_tokens"]) if row.get("output_tokens", "").strip() else None,
            }


def train(
    trials_path: Path = DEFAULT_TRIALS,
    artifact_path: Path = DEFAULT_ARTIFACT,
    tiers_path: Path = DEFAULT_TIERS,
    *,
    max_features: int = 900,
    epochs: int = 6,
) -> dict[str, object]:
    """Fit deterministic sparse logistic models and write a JSON artifact."""

    configurations = _load_tier_configs(tiers_path)
    rows = list(_iter_rows(trials_path, configurations))
    if not rows:
        raise RuntimeError(f"no usable advisor training rows in {trials_path}")

    document_frequency: Counter[str] = Counter()
    family_frequency: dict[str, Counter[str]] = defaultdict(Counter)
    family_docs: Counter[str] = Counter()
    for row in rows:
        features = set(text_features(str(row["prompt"])))
        document_frequency.update(features)
        family_frequency[str(row["family"])].update(features)
        family_docs[str(row["family"])] += 1

    maximum_df = len(rows) * 0.90
    selected = [
        token for token, count in document_frequency.most_common()
        if count >= 8 and count <= maximum_df
    ][:max_features]
    vocabulary = {token: index for index, token in enumerate(selected)}

    encoded: dict[str, list[tuple[list[int], float, float, int, float | None]]] = defaultdict(list)
    for row in rows:
        indices = [vocabulary[token] for token in text_features(str(row["prompt"])) if token in vocabulary]
        encoded[str(row["configuration"])].append(
            (indices, math.log1p(int(row["words"])) / 6.0, math.log1p(int(row["lines"])) / 3.0,
             int(bool(row["passed"])), row["output_tokens"] if isinstance(row["output_tokens"], float) else None)
        )

    models: dict[str, object] = {}
    for config in sorted(configurations):
        examples = encoded.get(config, [])
        if not examples:
            raise RuntimeError(f"no rows for configured tier backend {config}")
        positives = sum(item[3] for item in examples)
        base_rate = (positives + 1.0) / (len(examples) + 2.0)
        weights = [0.0] * (len(vocabulary) + 2)
        intercept = math.log(base_rate / (1.0 - base_rate))
        order = list(range(len(examples)))
        rng = random.Random(20261118)
        for epoch in range(epochs):
            rng.shuffle(order)
            learning_rate = 0.035 / math.sqrt(epoch + 1.0)
            for position in order:
                indices, word_length, line_length, target, _ = examples[position]
                score = intercept + weights[-2] * word_length + weights[-1] * line_length
                score += sum(weights[index] for index in indices)
                error = target - _sigmoid(score)
                intercept += learning_rate * error
                for index in indices:
                    weights[index] += learning_rate * (error - 0.0008 * weights[index])
                weights[-2] += learning_rate * (error * word_length - 0.0008 * weights[-2])
                weights[-1] += learning_rate * (error * line_length - 0.0008 * weights[-1])

        output_values = [item[4] for item in examples if item[4] is not None]
        models[config] = {
            "n": len(examples),
            "base_rate": round(base_rate, 8),
            "intercept": round(intercept, 8),
            "weights": [round(value, 7) for value in weights],
            "mean_output_tokens": round(sum(output_values) / len(output_values), 2) if output_values else 500.0,
            "mean_prompt_length_feature": round(sum(item[1] for item in examples) / len(examples), 4),
        }

    # These signatures support an intentionally conservative OOD flag.  They
    # are learned family-distinctive terms, not a hand-maintained taxonomy.
    signatures: dict[str, list[str]] = {}
    for family, counts in family_frequency.items():
        family_n = family_docs[family]
        ranked = sorted(
            counts,
            key=lambda token: (counts[token] / family_n) - (document_frequency[token] / len(rows)),
            reverse=True,
        )
        signatures[family] = [token for token in ranked if token in vocabulary][:30]

    artifact: dict[str, object] = {
        "artifact_version": 1,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "source": str(trials_path.relative_to(ROOT)) if trials_path.is_relative_to(ROOT) else str(trials_path),
        "training_rows": len(rows),
        "families": sorted(family_docs),
        "vocabulary": selected,
        "models": models,
        "family_signatures": signatures,
    }
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(artifact, separators=(",", ":")), encoding="utf-8")
    return artifact

