"""RAM-only hashing/projection and synthetic-trained semantic centroids."""
import csv
import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path
import random
import re

ARTIFACT = Path(__file__).with_name("artifacts")/"clusters.json"


@lru_cache(maxsize=1)
def projection():
    rng = random.Random(61119)
    return [[rng.choice((-1., 1.))/math.sqrt(32) for _ in range(32)] for _ in range(256)]


def normalize(v):
    norm = math.sqrt(sum(x*x for x in v)) or 1.
    return [x/norm for x in v]


def hashed_vector(text):
    bag = [0.]*256
    for word in re.findall(r"\w+", text.lower()):
        h = int.from_bytes(hashlib.blake2b(word.encode(), digest_size=8).digest(), "big")
        bag[h % 256] += 1 if (h >> 8) & 1 else -1
    return normalize([sum(b*row[j] for b,row in zip(bag,projection())) for j in range(32)])


@lru_cache(maxsize=1)
def cached_minilm():
    # Only an already cached model is permitted; never download or save embeddings.
    try:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2", local_files_only=True, device="cpu")
    except Exception:
        return None


@lru_cache(maxsize=1)
def artifact():
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def cluster(text, fast=False):
    data = artifact()
    # Optional encoder needs centroids fitted in the SAME embedding space.
    if not fast and data.get("minilm_centroids"):
        model = cached_minilm()
        if model is not None:
            v = model.encode(text, normalize_embeddings=True).tolist()
            return nearest(v, data["minilm_centroids"]), "minilm"
    return nearest(hashed_vector(text), data["centroids"]), "hash"


def nearest(v, centers):
    return max(range(len(centers)), key=lambda i: sum(a*b for a,b in zip(v,centers[i])))


def fit_centers(vectors, k=64, iterations=8):
    rng = random.Random(61119)
    centers = [list(v) for v in rng.sample(vectors, k)]
    for _ in range(iterations):
        sums = [[0.]*len(vectors[0]) for _ in range(k)]
        counts = [0]*k
        for v in vectors:
            i = nearest(v, centers); counts[i] += 1
            sums[i] = [a+b for a,b in zip(sums[i],v)]
        centers = [normalize(sums[i]) if counts[i] else centers[i] for i in range(k)]
    return centers


def train_clusters(path, optional_minilm=False):
    # Deterministic reservoir over unique synthetic trial prompts. No BenchLM input.
    prompts, seen = [], set()
    rng = random.Random(61119)
    with Path(path).open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            text = row["prompt"]
            if text in seen: continue
            seen.add(text)
            if len(prompts) < 2048: prompts.append(text)
            else:
                i = rng.randrange(len(seen))
                if i < len(prompts): prompts[i] = text
    data = {"version": 1, "k": 64, "seed": 61119, "training_prompts": len(prompts),
            "source": "repository synthetic trials", "centroids": fit_centers([hashed_vector(p) for p in prompts])}
    if optional_minilm:
        model = cached_minilm()
        if model is not None:
            data["minilm_centroids"] = fit_centers(model.encode(prompts, normalize_embeddings=True).tolist(), iterations=4)
    ARTIFACT.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    artifact.cache_clear()
    return data
