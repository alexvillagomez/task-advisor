"""Redaction and stable pseudonymization primitives.

Only redacted text and one-way identifiers produced here may leave parser memory.
"""

from __future__ import annotations

import getpass
import hashlib
import os
import re
from collections import Counter
from pathlib import Path, PurePath
from typing import Iterable, Mapping


REDACTION_VERSION = "redaction-v1.3.0"
REDACTION_TYPES = (
    "private_key",
    "env_secret",
    "bearer_token",
    "jwt",
    "api_key",
    "password",
    "email",
    "phone",
    "absolute_path",
    "username",
    "repo_name",
)

_PRIVATE_KEY = re.compile(
    r"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----.*?"
    r"-----END(?: [A-Z0-9]+)* PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_ENV_SECRET = re.compile(
    r"(?m)^[ \t]*(?:(?i:export)[ \t]+)?(?:"
    r"[A-Z_][A-Z0-9_]*=|"
    r"(?i:[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD|AUTH))[ \t]*="
    r")[^\r\n]*$",
    re.MULTILINE,
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b")
_API_KEY = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"sk-[A-Za-z0-9_-]{8,}|"
    r"gh[A-Za-z0-9]*_[A-Za-z0-9]{8,}|"
    r"AKIA[A-Z0-9]{12,}|"
    r"xox[A-Za-z0-9]*-[A-Za-z0-9-]{8,}"
    r")"
)
_PASSWORD = re.compile(
    r"(?i)[\"']?\b(password|passwd|passphrase|pwd)\b[\"']?\s*(?::|=|\bis\b)\s*"
    r"(?:\"[^\"\r\n]+\"|'[^'\r\n]+'|[^\s,;]+)"
)
_EMAIL = re.compile(
    r"[A-Z0-9][A-Z0-9._%+-]*@"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)*"
    # A period or hyphen only continues the domain when it starts another
    # label.  This leaves sentence-final punctuation outside the match while
    # still preventing a match from stopping partway through a domain.
    r"(?![A-Z0-9_]|[.-][A-Z0-9])",
    re.I,
)
_PHONE = re.compile(r"(?<!\w)\+?\d[\d ().-]{5,}\d(?!\w)")
_DOCUMENTATION_DOMAINS = frozenset({"example.com", "example.org", "example.net"})
_DOCUMENTATION_TLDS = frozenset({"test", "local", "invalid"})
_QUOTED_ABS = re.compile(
    r"(?i)(?P<quote>[\"'`])(?:[A-Z]:[\\/]|/)(?:[^\r\n\"'`<>|]+)(?P=quote)"
)
_WINDOWS_ABS_SPACED = re.compile(
    r"""(?ix)(?<![\w])
    [A-Z]:[\\/](?:[^\\/\r\n<>:"|?*]+[\\/])*
    [^\\/\r\n<>:"|?*]+?\.[A-Za-z0-9]{1,12}
    (?=$|[\s,;:)\]}`'"])
    """
)
_WINDOWS_ABS = re.compile(
    r"(?i)(?<![\w])(?:[A-Z]:[\\/](?:[^\s<>:\"|?*\r\n]+[\\/])*[^\s<>:\"|?*\r\n]*)"
)
_UNC_ABS = re.compile(r"(?<![\w])(?:\\\\[^\\\s]+\\[^\s<>:\"|?*\r\n]+)")
_POSIX_ABS = re.compile(r"(?<![\w.])/(?:[^/\s\x00]+/)*[^/\s\x00]*")


def stable_hash(value: str, *, namespace: str = "value", length: int = 16) -> str:
    normalized = value.replace("\\", "/").casefold()
    payload = f"ingest-v1:{namespace}:{normalized}".encode("utf-8", "surrogatepass")
    return hashlib.sha256(payload).hexdigest()[:length]


def _safe_extension(value: str) -> str:
    cleaned = value.rstrip("/\\.,;:)]}'\"")
    suffix = PurePath(cleaned.replace("\\", "/")).suffix
    if re.fullmatch(r"\.[A-Za-z0-9]{1,12}", suffix or ""):
        return suffix.lower()
    return ""


def hash_path(value: str) -> str:
    """Return a stable path pseudonym while retaining only a safe extension."""

    return f"path:{stable_hash(value, namespace='path')}{_safe_extension(value)}"


def hash_identifier(value: str, kind: str) -> str:
    return f"{kind}:{stable_hash(value, namespace=kind)}{_safe_extension(value)}"


def local_identity_names() -> tuple[str, ...]:
    """Return local account/display names without persisting them anywhere."""

    candidates: set[str] = set()
    for value in (
        Path.home().name,
        os.environ.get("USERNAME"),
        os.environ.get("USER"),
        os.environ.get("LOGNAME"),
        PurePath(os.environ.get("USERPROFILE", "")).name,
    ):
        if value and len(value.strip()) >= 2:
            candidates.add(value.strip())
    try:
        account = getpass.getuser().strip()
    except (ImportError, KeyError, OSError):
        account = ""
    if len(account) >= 2:
        candidates.add(account)
    return tuple(sorted(candidates, key=len, reverse=True))


def is_documentation_email(value: str) -> bool:
    domain = value.rsplit("@", 1)[-1].casefold().rstrip(".")
    if domain in _DOCUMENTATION_DOMAINS:
        return True
    if any(domain.endswith("." + item) for item in _DOCUMENTATION_DOMAINS):
        return True
    tld = domain.rsplit(".", 1)[-1]
    return tld in _DOCUMENTATION_TLDS


def iter_non_allowlisted_emails(text: str) -> Iterable[re.Match[str]]:
    return (match for match in _EMAIL.finditer(text) if not is_documentation_email(match.group(0)))


def iter_phone_numbers(text: str) -> Iterable[re.Match[str]]:
    return (
        match
        for match in _PHONE.finditer(text)
        if 10 <= sum(character.isdigit() for character in match.group(0)) <= 15
    )


class Redactor:
    """Stateful redactor that counts each replacement by privacy category."""

    def __init__(
        self,
        *,
        usernames: Iterable[str] = (),
        repo_names: Iterable[str] = (),
    ) -> None:
        self.counts: Counter[str] = Counter()
        identity_names = set(local_identity_names())
        identity_names.update(x for x in usernames if len(x) >= 2)
        self.usernames = tuple(sorted(identity_names, key=len, reverse=True))
        self.repo_names = tuple(sorted({x for x in repo_names if len(x) >= 2}, key=len, reverse=True))

    def _sub(self, pattern: re.Pattern[str], text: str, kind: str, replacement: str) -> str:
        def replace(_: re.Match[str]) -> str:
            self.counts[kind] += 1
            return replacement

        return pattern.sub(replace, text)

    def _named(
        self,
        text: str,
        values: Iterable[str],
        kind: str,
        *,
        any_context: bool = False,
    ) -> str:
        for value in values:
            if any_context:
                pattern = re.compile(re.escape(value), re.I)
            else:
                pattern = re.compile(rf"(?<![\w]){re.escape(value)}(?![\w])", re.I)

            def replace(match: re.Match[str], *, _kind: str = kind) -> str:
                self.counts[_kind] += 1
                label = "identity" if _kind == "username" else _kind
                return f"{label}_hash_{stable_hash(match.group(0), namespace=_kind)}"

            text = pattern.sub(replace, text)
        return text

    def redact(self, value: object) -> str:
        text = "" if value is None else str(value)
        text = self._sub(_PRIVATE_KEY, text, "private_key", "<private_key:redacted>")
        text = self._sub(_ENV_SECRET, text, "env_secret", "<env_secret:redacted>")
        text = self._sub(_BEARER, text, "bearer_token", "<bearer_token:redacted>")
        text = self._sub(_JWT, text, "jwt", "<jwt:redacted>")
        text = self._sub(_API_KEY, text, "api_key", "<api_key:redacted>")

        def password_replace(match: re.Match[str]) -> str:
            self.counts["password"] += 1
            return f"{match.group(1)}=<password:redacted>"

        text = _PASSWORD.sub(password_replace, text)

        def email_replace(match: re.Match[str]) -> str:
            if is_documentation_email(match.group(0)):
                return match.group(0)
            self.counts["email"] += 1
            return "<email:redacted>"

        text = _EMAIL.sub(email_replace, text)

        def phone_replace(match: re.Match[str]) -> str:
            digit_count = sum(character.isdigit() for character in match.group(0))
            if 10 <= digit_count <= 15:
                self.counts["phone"] += 1
                return "<phone:redacted>"
            return match.group(0)

        text = _PHONE.sub(phone_replace, text)
        # Pseudonymize known path segments before path matching.  The safe
        # alphanumeric replacements also handle usernames/repositories that
        # contain spaces without breaking the surrounding absolute path.
        text = self._named(text, self.usernames, "username", any_context=True)
        text = self._named(text, self.repo_names, "repo_name")

        def path_replace(match: re.Match[str]) -> str:
            raw = match.group(0)
            self.counts["absolute_path"] += 1
            return f"<{hash_path(raw)}>"

        text = _QUOTED_ABS.sub(path_replace, text)
        text = _WINDOWS_ABS_SPACED.sub(path_replace, text)
        text = _WINDOWS_ABS.sub(path_replace, text)
        text = _UNC_ABS.sub(path_replace, text)
        text = _POSIX_ABS.sub(path_replace, text)
        return text

    def report(self) -> dict[str, int]:
        return {kind: int(self.counts.get(kind, 0)) for kind in REDACTION_TYPES}


def merge_reports(reports: Iterable[Mapping[str, int]]) -> dict[str, int]:
    total: Counter[str] = Counter()
    for report in reports:
        total.update({key: int(value) for key, value in report.items()})
    return {kind: int(total.get(kind, 0)) for kind in REDACTION_TYPES}
