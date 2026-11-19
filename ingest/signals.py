"""Deterministic feature, follow-up, and execution-signal extraction."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Iterable


CORRECTION_WORDS = (
    "no",
    "wrong",
    "still",
    "doesn't work",
    "does not work",
    "revert",
    "undo",
    "broken",
    "not what i asked",
    "try again",
)
COMPLAINT_WORDS = ("bad", "worse", "failed", "failure", "incorrect", "you missed")
APPROVAL_WORDS = ("thanks", "thank you", "works", "perfect", "great", "looks good", "nice")
INTERRUPTION_WORDS = ("stop", "cancel", "wait", "hold on", "never mind", "nevermind", "abort")

_WORD = re.compile(r"[A-Za-z0-9_]+")
_PATH_MENTION = re.compile(
    r"(?i)(?:[A-Z]:[\\/]|(?:^|\s)/(?:[^\s/]+/)+|(?:^|\s)(?:[\w.-]+[\\/]){1,}[\w.-]+)"
)


def word_tokens(text: str) -> list[str]:
    return [token.casefold() for token in _WORD.findall(text)]


def token_jaccard(left: str, right: str) -> float:
    a, b = set(word_tokens(left)), set(word_tokens(right))
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _ngrams(text: str, n: int = 3) -> Counter[str]:
    normalized = " ".join(text.casefold().split())
    if len(normalized) < n:
        return Counter({normalized: 1}) if normalized else Counter()
    return Counter(normalized[index : index + n] for index in range(len(normalized) - n + 1))


def char_ngram_cosine(left: str, right: str) -> float:
    a, b = _ngrams(left), _ngrams(right)
    if not a or not b:
        return 0.0
    dot = sum(value * b.get(key, 0) for key, value in a.items())
    denom = math.sqrt(sum(value * value for value in a.values())) * math.sqrt(
        sum(value * value for value in b.values())
    )
    return dot / denom if denom else 0.0


def retry_similarity(left: str, right: str) -> float:
    return max(token_jaccard(left, right), char_ngram_cosine(left, right))


def _has_phrase(text: str, phrases: Iterable[str]) -> bool:
    for phrase in phrases:
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text, re.I):
            return True
    return False


def classify_followup(current_prompt: str, next_prompt: str) -> dict[str, object]:
    lowered = next_prompt.casefold().strip()
    similarity = retry_similarity(current_prompt, next_prompt)
    is_retry = similarity >= 0.65 and len(word_tokens(next_prompt)) >= 3
    markers: list[str] = []
    if _has_phrase(lowered, CORRECTION_WORDS):
        classification = "correction"
        markers = [word for word in CORRECTION_WORDS if _has_phrase(lowered, (word,))]
    elif _has_phrase(lowered, COMPLAINT_WORDS):
        classification = "complaint"
        markers = [word for word in COMPLAINT_WORDS if _has_phrase(lowered, (word,))]
    elif _has_phrase(lowered, INTERRUPTION_WORDS):
        classification = "interruption"
        markers = [word for word in INTERRUPTION_WORDS if _has_phrase(lowered, (word,))]
    elif _has_phrase(lowered, APPROVAL_WORDS):
        classification = "approval"
        markers = [word for word in APPROVAL_WORDS if _has_phrase(lowered, (word,))]
    elif is_retry:
        classification = "retry"
    else:
        classification = "new_topic"
    return {
        "classification": classification,
        "markers": markers,
        "retry_similarity": round(similarity, 6),
        "is_retry": is_retry,
    }


def count_path_mentions(text: str) -> int:
    return len(_PATH_MENTION.findall(text))


_COMMAND_PREFIX = re.compile(
    r"^(?:\s*(?:env\s+)?(?:[A-Za-z_][A-Za-z0-9_]*=[^\s]+\s+)*)"
    r"(?:&\s*)?(?:(?:uv|poetry|pipenv)\s+run\s+)?",
    re.I,
)

_CHECK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pytest", re.compile(r"^(?:python(?:3(?:\.\d+)?)?\s+-m\s+)?pytest(?:\s|$)", re.I)),
    ("unittest", re.compile(r"^python(?:3(?:\.\d+)?)?\s+-m\s+unittest(?:\s|$)", re.I)),
    ("npm_test", re.compile(r"^(?:npm|pnpm|yarn)(?:\s+run)?\s+test(?:\s|$)", re.I)),
    ("jest", re.compile(r"^(?:npx\s+)?jest(?:\s|$)", re.I)),
    ("vitest", re.compile(r"^(?:npx\s+)?vitest(?:\s+run)?(?:\s|$)", re.I)),
    ("cargo_test", re.compile(r"^cargo\s+test(?:\s|$)", re.I)),
    ("go_test", re.compile(r"^go\s+test(?:\s|$)", re.I)),
    ("maven_test", re.compile(r"^(?:mvn|mvnw|\.\\mvnw|\./mvnw)(?:\s+[^;&|]+)?\s+test(?:\s|$)", re.I)),
    ("gradle_test", re.compile(r"^(?:gradle|gradlew|\.\\gradlew|\./gradlew)(?:\s+[^;&|]+)?\s+test(?:\s|$)", re.I)),
    ("tsc", re.compile(r"^(?:npx\s+)?tsc(?:\s|$)", re.I)),
    ("eslint", re.compile(r"^(?:npx\s+)?eslint(?:\s|$)", re.I)),
    ("ruff", re.compile(r"^(?:python(?:3)?\s+-m\s+)?ruff\s+(?:check|format\s+--check)(?:\s|$)", re.I)),
    ("mypy", re.compile(r"^(?:python(?:3)?\s+-m\s+)?mypy(?:\s|$)", re.I)),
    ("make", re.compile(r"^(?:make|gmake)\s+(?:test|check|lint|typecheck)(?:\s|$)", re.I)),
    ("dotnet_test", re.compile(r"^dotnet\s+test(?:\s|$)", re.I)),
    ("ctest", re.compile(r"^ctest(?:\s|$)", re.I)),
    ("tox", re.compile(r"^(?:python(?:3)?\s+-m\s+)?tox(?:\s|$)", re.I)),
    ("mocha", re.compile(r"^(?:npx\s+)?mocha(?:\s|$)", re.I)),
)


def detect_check_command(command: object) -> str | None:
    if isinstance(command, list):
        text = " ".join(str(part) for part in command)
    else:
        text = str(command or "")
    for segment in re.split(r"(?:&&|\|\||;|\r?\n)", text):
        normalized = _COMMAND_PREFIX.sub("", segment.strip())
        for family, pattern in _CHECK_PATTERNS:
            if pattern.search(normalized):
                return family
    return None


def normalize_tool_type(name: object) -> str:
    value = str(name or "").casefold()
    if any(part in value for part in ("command", "shell", "bash", "terminal", "exec")):
        return "command"
    if any(part in value for part in ("patch", "edit", "write", "notebook", "filechange", "file_change")):
        return "edit"
    if any(part in value for part in ("read", "view", "open_file", "get_file")):
        return "read"
    if any(part in value for part in ("search", "grep", "glob", "find")):
        return "search"
    if any(part in value for part in ("browser", "web", "fetch", "http")):
        return "web"
    if any(part in value for part in ("agent", "collaboration", "task")):
        return "subagent"
    if value.startswith("mcp") or "__" in value:
        return "mcp"
    return "other"


def result_from_status(*, exit_code: object = None, is_error: object = None, status: object = None) -> str:
    if exit_code is not None:
        try:
            return "pass" if int(exit_code) == 0 else "fail"
        except (TypeError, ValueError):
            pass
    if isinstance(is_error, bool):
        return "fail" if is_error else "pass"
    normalized = str(status or "").casefold()
    if normalized in {"failed", "error", "timed_out", "timeout", "declined", "cancelled"}:
        return "fail"
    if normalized in {"completed", "success", "succeeded", "ok", "passed"}:
        return "pass"
    return "unknown"
