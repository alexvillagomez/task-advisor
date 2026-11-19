"""Streaming normalization of Codex and Claude transcripts into episodes."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .outcomes import RULES_VERSION, apply_outcome_rules
from .privacy import REDACTION_TYPES, REDACTION_VERSION, Redactor, hash_path, stable_hash
from .readers import iter_jsonl
from .signals import (
    classify_followup,
    count_path_mentions,
    detect_check_command,
    normalize_tool_type,
    result_from_status,
    word_tokens,
)


SCHEMA_VERSION = "episode-v1.2.0"
_PATCH_PATH = re.compile(r"(?m)^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$")
_ABS_WINDOWS = re.compile(r"^[A-Za-z]:[\\/]")
_CONTEXT_ONLY = re.compile(
    r"^\s*<environment_context>.*?</environment_context>\s*$",
    re.IGNORECASE | re.DOTALL,
)


def _timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000.0
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return _timestamp(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _milliseconds(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return max(0, round((end - start).total_seconds() * 1000))


def _content_text(content: object, *, include_tool_results: bool = False) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    pieces: list[str] = []
    for block in content:
        if isinstance(block, str):
            pieces.append(block)
            continue
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type", "")).casefold()
        if block_type in {"text", "input_text", "output_text"}:
            value = block.get("text")
            if isinstance(value, str):
                pieces.append(value)
        elif include_tool_results and block_type == "tool_result":
            value = block.get("content")
            if isinstance(value, str):
                pieces.append(value)
    return "\n".join(piece for piece in pieces if piece)


def _is_human_content(content: object) -> bool:
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list):
        return False
    types = {
        str(block.get("type", "")).casefold()
        for block in content
        if isinstance(block, dict)
    }
    return bool(types & {"text", "input_text"}) and "tool_result" not in types


def _usage_values(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    aliases = {
        "input": ("input_tokens", "input", "prompt_tokens"),
        "cached_input": (
            "cached_input_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ),
        "output": ("output_tokens", "output", "completion_tokens"),
        "reasoning": ("reasoning_output_tokens", "reasoning_tokens", "thinking_tokens"),
    }
    result: dict[str, int] = {}
    found = False
    for target, keys in aliases.items():
        total = 0
        for key in keys:
            item = value.get(key)
            if isinstance(item, (int, float)) and item >= 0:
                total += int(item)
                found = True
        result[target] = total
    return result if found else None


def _find_cumulative_usage(payload: object) -> dict[str, int] | None:
    if not isinstance(payload, dict):
        return None
    preferred: list[object] = [payload]
    info = payload.get("info")
    if isinstance(info, dict):
        preferred = [info.get("total_token_usage"), info.get("token_usage"), info] + preferred
    preferred = [payload.get("total_token_usage"), payload.get("token_usage")] + preferred
    for candidate in preferred:
        usage = _usage_values(candidate)
        if usage is not None:
            return usage
    for value in payload.values():
        if isinstance(value, dict):
            usage = _find_cumulative_usage(value)
            if usage is not None:
                return usage
    return None


def _structured_exit_code(value: object) -> object:
    if not isinstance(value, dict):
        return None
    for key in ("exit_code", "exitCode", "code"):
        candidate = value.get(key)
        if isinstance(candidate, (int, float)):
            return candidate
    for key in ("result", "toolUseResult", "metadata"):
        nested = value.get(key)
        candidate = _structured_exit_code(nested)
        if candidate is not None:
            return candidate
    return None


def _structured_is_error(value: object) -> bool | None:
    if not isinstance(value, dict):
        return None
    for key in ("is_error", "isError"):
        candidate = value.get(key)
        if isinstance(candidate, bool):
            return candidate
    for key in ("result", "toolUseResult", "metadata"):
        nested = _structured_is_error(value.get(key))
        if nested is not None:
            return nested
    return None


def _command_from(value: object) -> object:
    if not isinstance(value, dict):
        return None
    for key in ("command", "cmd"):
        command = value.get(key)
        if isinstance(command, (str, list)):
            return command
    action = value.get("action")
    if isinstance(action, dict):
        command = _command_from(action)
        if command is not None:
            return command
    return None


def _paths_from(value: object, *, depth: int = 0) -> list[str]:
    if depth > 4 or not isinstance(value, dict):
        return []
    results: list[str] = []
    path_keys = {
        "path",
        "file_path",
        "filepath",
        "file",
        "filename",
        "target_file",
        "notebook_path",
    }
    for key, item in value.items():
        if key.casefold() in path_keys and isinstance(item, str) and item.strip():
            results.append(item.strip())
        elif key.casefold() in {"changes", "files", "input", "arguments", "metadata"}:
            if isinstance(item, dict):
                results.extend(_paths_from(item, depth=depth + 1))
            elif isinstance(item, list):
                for nested in item:
                    if isinstance(nested, dict):
                        results.extend(_paths_from(nested, depth=depth + 1))
    patch = value.get("patch") or value.get("input")
    if isinstance(patch, str) and "*** " in patch:
        results.extend(match.group(1).strip() for match in _PATCH_PATH.finditer(patch))
    return results


def _repo_and_user_names(cwds: Iterable[str], repo_hints: Iterable[str]) -> tuple[set[str], set[str]]:
    repos: set[str] = set()
    users: set[str] = set()
    for raw in cwds:
        normalized = raw.replace("\\", "/").rstrip("/")
        parts = [part for part in normalized.split("/") if part and not part.endswith(":")]
        if parts:
            repos.add(parts[-1])
        lowered = [part.casefold() for part in parts]
        for marker in ("users", "home"):
            if marker in lowered:
                index = lowered.index(marker)
                if index + 1 < len(parts):
                    users.add(parts[index + 1])
    for hint in repo_hints:
        normalized = hint.replace("\\", "/").rstrip("/")
        name = normalized.rsplit("/", 1)[-1]
        if name.endswith(".git"):
            name = name[:-4]
        if name:
            repos.add(name)
    return repos, users


def is_excluded_cwd(cwd: str, workspace_root: Path) -> bool:
    raw = cwd.replace("\\", "/").rstrip("/").casefold()
    workspace = str(workspace_root.resolve()).replace("\\", "/").rstrip("/").casefold()
    if raw == workspace or raw.startswith(workspace + "/"):
        return True
    parts = [part for part in raw.split("/") if part]
    project = workspace_root.name.casefold()
    return project in parts


def _prompt_token_count(text: str) -> int:
    # Keep ingestion offline.  This reversible approximation is preferable to
    # letting tiktoken fetch an encoder when its local cache is absent.
    return len(re.findall(r"\w+|[^\w\s]", text, re.UNICODE))


@dataclass
class EpisodeWork:
    prompt: str
    request_at: datetime | None
    turn_index: int
    baseline_usage: dict[str, int]
    model: str | None = None
    effort: str | None = None
    first_agent_at: datetime | None = None
    final_agent_at: datetime | None = None
    explicit_duration_ms: int | None = None
    latest_usage: dict[str, int] | None = None
    summed_usage: Counter[str] = field(default_factory=Counter)
    tool_calls_by_type: Counter[str] = field(default_factory=Counter)
    tool_calls: int = 0
    command_count: int = 0
    error_count: int = 0
    nonzero_exit_count: int = 0
    checks: list[dict[str, object]] = field(default_factory=list)
    files_edited: set[str] = field(default_factory=set)
    seen_tool_ids: set[str] = field(default_factory=set)
    pending_tools: dict[str, dict[str, object]] = field(default_factory=dict)
    seen_usage_ids: set[str] = field(default_factory=set)

    def observe_agent(self, at: datetime | None, *, final: bool = False) -> None:
        if at is not None and self.first_agent_at is None:
            self.first_agent_at = at
        if at is not None and (final or self.final_agent_at is None or at >= self.final_agent_at):
            self.final_agent_at = at

    def add_tool(
        self,
        name: object,
        at: datetime | None,
        *,
        call_id: object = None,
        command: object = None,
        inputs: object = None,
    ) -> str:
        signature = str(call_id or "")
        if not signature:
            summary = json.dumps([str(name), command], sort_keys=True, default=str)
            signature = f"anon:{stable_hash(summary, namespace='tool-call')}:{self.tool_calls}"
        if signature in self.seen_tool_ids:
            return signature
        self.seen_tool_ids.add(signature)
        tool_type = normalize_tool_type(name)
        self.tool_calls_by_type[tool_type] += 1
        self.tool_calls += 1
        if command is None:
            command = _command_from(inputs)
        family = detect_check_command(command) if command is not None else None
        if command is not None or tool_type == "command":
            self.command_count += 1
        for path in _paths_from(inputs):
            self.files_edited.add(path)
        self.pending_tools[signature] = {"family": family, "at": at, "command": command}
        self.observe_agent(at)
        return signature

    def finish_tool(
        self,
        call_id: object,
        at: datetime | None,
        *,
        exit_code: object = None,
        is_error: object = None,
        status: object = None,
        command: object = None,
        name: object = None,
        result_data: object = None,
    ) -> None:
        signature = str(call_id or "")
        pending = self.pending_tools.get(signature)
        if pending is None and (command is not None or name is not None):
            signature = self.add_tool(name or "command", at, call_id=call_id, command=command)
            pending = self.pending_tools.get(signature)
        pending = pending or {}
        if exit_code is None:
            exit_code = _structured_exit_code(result_data)
        result = result_from_status(exit_code=exit_code, is_error=is_error, status=status)
        try:
            if exit_code is not None and int(exit_code) != 0:
                self.nonzero_exit_count += 1
        except (TypeError, ValueError):
            pass
        normalized_status = str(status or "").casefold()
        if is_error is True or normalized_status in {
            "failed",
            "error",
            "timed_out",
            "timeout",
            "declined",
            "cancelled",
        }:
            self.error_count += 1
        family = pending.get("family")
        if family:
            self.checks.append({"family": family, "result": result, "timestamp": _iso(at)})
        for path in _paths_from(result_data):
            self.files_edited.add(path)
        self.observe_agent(at)

    def add_summed_usage(self, usage: dict[str, int] | None, record_id: object = None) -> None:
        if usage is None:
            return
        identity = str(record_id or "")
        if identity and identity in self.seen_usage_ids:
            return
        if identity:
            self.seen_usage_ids.add(identity)
        self.summed_usage.update(usage)


@dataclass
class SessionWork:
    source: str
    source_file_hash: str
    session_id: str
    workspace_root: Path
    episodes: list[EpisodeWork] = field(default_factory=list)
    current: EpisodeWork | None = None
    latest_usage: dict[str, int] = field(
        default_factory=lambda: {"input": 0, "cached_input": 0, "output": 0, "reasoning": 0}
    )
    current_model: str | None = None
    current_effort: str | None = None
    cwds: set[str] = field(default_factory=set)
    repo_roots: set[str] = field(default_factory=set)
    repo_hints: set[str] = field(default_factory=set)
    seen_prompts: set[str] = field(default_factory=set)

    def add_prompt(self, prompt: str, at: datetime | None) -> None:
        prompt = prompt.strip()
        if not prompt or _CONTEXT_ONLY.fullmatch(prompt):
            return
        if (
            self.current is not None
            and self.current.prompt == prompt
            and self.current.first_agent_at is None
            and self.current.tool_calls == 0
        ):
            return
        stamp = _iso(at) or "none"
        dedupe = stable_hash(f"{stamp}:{prompt}", namespace="prompt-record")
        if dedupe in self.seen_prompts:
            return
        self.seen_prompts.add(dedupe)
        if self.current is not None:
            self.episodes.append(self.current)
        self.current = EpisodeWork(
            prompt=prompt,
            request_at=at,
            turn_index=len(self.episodes),
            baseline_usage=dict(self.latest_usage),
            model=self.current_model,
            effort=self.current_effort,
        )

    def complete(self) -> None:
        if self.current is not None:
            self.episodes.append(self.current)
            self.current = None

    def update_cumulative_usage(self, usage: dict[str, int] | None) -> None:
        if usage is None:
            return
        self.latest_usage = usage
        if self.current is not None:
            self.current.latest_usage = usage

    def excluded(self) -> bool:
        return any(is_excluded_cwd(cwd, self.workspace_root) for cwd in self.cwds)

    def normalized(self) -> list[dict[str, Any]]:
        self.complete()
        if self.excluded():
            return []
        repos, users = _repo_and_user_names(self.cwds | self.repo_roots, self.repo_hints)
        raw_prompts = [episode.prompt for episode in self.episodes]
        followups: list[dict[str, object]] = []
        for index, prompt in enumerate(raw_prompts):
            if index + 1 < len(raw_prompts):
                followups.append(classify_followup(prompt, raw_prompts[index + 1]))
            else:
                followups.append(
                    {
                        "classification": "none",
                        "markers": [],
                        "retry_similarity": None,
                        "is_retry": False,
                    }
                )

        output: list[dict[str, Any]] = []
        for index, work in enumerate(self.episodes):
            redactor = Redactor(usernames=users, repo_names=repos)
            prompt_text = redactor.redact(work.prompt)
            cwd = sorted(self.cwds)[0] if self.cwds else ""
            repo_root = sorted(self.repo_roots)[0] if self.repo_roots else cwd
            if cwd:
                if _ABS_WINDOWS.match(cwd) or cwd.startswith("/") or cwd.startswith("\\\\"):
                    redactor.counts["absolute_path"] += 1
                cwd_hash = hash_path(cwd)
            else:
                cwd_hash = None
            if repo_root:
                if _ABS_WINDOWS.match(repo_root) or repo_root.startswith("/") or repo_root.startswith("\\\\"):
                    redactor.counts["absolute_path"] += 1
                repo_hash = hash_path(repo_root)
            else:
                repo_hash = None
            edited: list[str] = []
            for path in sorted(work.files_edited):
                if _ABS_WINDOWS.match(path) or path.startswith("/") or path.startswith("\\\\"):
                    redactor.counts["absolute_path"] += 1
                edited.append(hash_path(path))

            if self.source == "codex" and work.latest_usage is not None:
                tokens = {
                    key: max(0, int(work.latest_usage.get(key, 0)) - int(work.baseline_usage.get(key, 0)))
                    for key in ("input", "cached_input", "output", "reasoning")
                }
            else:
                tokens = {
                    key: int(work.summed_usage.get(key, 0))
                    for key in ("input", "cached_input", "output", "reasoning")
                }
            checks = work.checks
            first_check = checks[0] if checks else None
            last_check = checks[-1] if checks else None
            fail_to_pass = any(check["result"] == "fail" for check in checks) and any(
                check["result"] == "pass"
                for check in checks[
                    next(
                        (i for i, check in enumerate(checks) if check["result"] == "fail"),
                        len(checks),
                    )
                    + 1 :
                ]
            )
            resolution = 0
            if index + 1 >= len(self.episodes):
                resolution_value: int | None = None
            else:
                for offset in range(index, len(followups) - 1):
                    relation = followups[offset]["classification"]
                    if relation == "new_topic":
                        break
                    resolution += 1
                    if relation == "approval":
                        break
                resolution_value = resolution
            next_at = self.episodes[index + 1].request_at if index + 1 < len(self.episodes) else None
            final_at = work.final_agent_at
            duration_ms = _milliseconds(work.request_at, final_at)
            if duration_ms is None:
                duration_ms = work.explicit_duration_ms
            episode_id = stable_hash(
                f"{self.source}:{self.session_id}:{work.turn_index}:{_iso(work.request_at)}",
                namespace="episode",
                length=24,
            )
            episode: dict[str, Any] = {
                "schema_version": SCHEMA_VERSION,
                "redaction_version": REDACTION_VERSION,
                "rules_version": RULES_VERSION,
                "episode_id": episode_id,
                "source": self.source,
                "source_file_hash": self.source_file_hash,
                "session_id_hash": stable_hash(self.session_id, namespace="session", length=24),
                "turn_index": work.turn_index,
                "request_at": _iso(work.request_at),
                "model": redactor.redact(work.model) if work.model else None,
                "reasoning_effort": redactor.redact(work.effort) if work.effort else None,
                "prompt_text": prompt_text,
                "context": {
                    "prior_turns": work.turn_index,
                    "prior_tokens": sum(int(value) for value in work.baseline_usage.values()),
                    "files_paths_mentioned": count_path_mentions(work.prompt),
                    "prompt_chars": len(work.prompt),
                    "prompt_words": len(word_tokens(work.prompt)),
                    "prompt_tokens": _prompt_token_count(work.prompt),
                },
                "timing": {
                    "agent_wall_time_ms": duration_ms,
                    "time_to_first_agent_output_ms": _milliseconds(work.request_at, work.first_agent_at),
                    "user_response_delay_ms": _milliseconds(final_at, next_at),
                    "first_agent_output_at": _iso(work.first_agent_at),
                    "final_agent_output_at": _iso(final_at),
                },
                "tokens": tokens,
                "tools": {
                    "calls_by_type": dict(sorted(work.tool_calls_by_type.items())),
                    "tool_calls": work.tool_calls,
                    "command_count": work.command_count,
                    "error_count": work.error_count,
                    "nonzero_exit_count": work.nonzero_exit_count,
                },
                "execution": {
                    "check_count": len(checks),
                    "first": first_check,
                    "last": last_check,
                    "fail_to_pass": fail_to_pass,
                    "ended_passing": bool(last_check and last_check["result"] == "pass"),
                    "checks": checks,
                },
                "followup": {**followups[index], "turns_to_resolution": resolution_value},
                "aftermath": {
                    "cwd_hash": cwd_hash,
                    "repo_root_hash": repo_hash,
                    "start_at": _iso(work.request_at),
                    "end_at": _iso(final_at),
                    "files_edited": sorted(set(edited)),
                },
            }
            tier, score, components = apply_outcome_rules(episode)
            episode["outcome_tier"] = tier
            episode["outcome_score"] = score
            episode["outcome_components"] = components
            episode["privacy"] = {"redactions": redactor.report()}
            output.append(episode)
        return output


def _session(
    sessions: dict[str, SessionWork],
    session_id: object,
    *,
    source: str,
    source_file_hash: str,
    workspace_root: Path,
) -> SessionWork:
    identity = str(session_id or source_file_hash)
    if identity not in sessions:
        sessions[identity] = SessionWork(source, source_file_hash, identity, workspace_root)
    return sessions[identity]


def parse_codex(
    path: Path, source_file_hash: str, workspace_root: Path, diagnostics: dict[str, int]
) -> list[dict[str, Any]]:
    sessions: dict[str, SessionWork] = {}
    current_id: str = source_file_hash
    for record in iter_jsonl(path, diagnostics):
        record_type = str(record.get("type", ""))
        payload = record.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        at = _timestamp(record.get("timestamp") or payload.get("timestamp"))
        if record_type == "session_meta":
            current_id = str(payload.get("id") or current_id)
        session = _session(
            sessions,
            payload.get("session_id") or payload.get("thread_id") or current_id,
            source="codex",
            source_file_hash=source_file_hash,
            workspace_root=workspace_root,
        )
        cwd = payload.get("cwd")
        if isinstance(cwd, str):
            session.cwds.add(cwd)
        git = payload.get("git")
        if isinstance(git, dict):
            repository = git.get("repository_url") or git.get("repo_root")
            if isinstance(repository, str):
                session.repo_hints.add(repository)
            repo_root = git.get("repo_root") or git.get("worktree")
            if isinstance(repo_root, str):
                session.repo_roots.add(repo_root)
        repo_root = payload.get("repo_root")
        if isinstance(repo_root, str):
            session.repo_roots.add(repo_root)
        workspace_roots = payload.get("runtime_workspace_roots")
        if isinstance(workspace_roots, list):
            session.repo_roots.update(str(root) for root in workspace_roots if isinstance(root, str))

        if record_type == "turn_context":
            session.current_model = str(payload.get("model")) if payload.get("model") else session.current_model
            session.current_effort = str(payload.get("effort")) if payload.get("effort") else session.current_effort
            if session.current is not None:
                session.current.model = session.current_model
                session.current.effort = session.current_effort
            continue

        if record_type in {"token_usage_record", "token_count"}:
            session.update_cumulative_usage(_find_cumulative_usage(payload))
            continue

        if record_type == "response_item":
            item_type = str(payload.get("type", "")).casefold()
            role = str(payload.get("role", "")).casefold()
            if item_type == "message" and role == "user":
                session.add_prompt(_content_text(payload.get("content")), at)
            elif item_type == "message" and role == "assistant" and session.current is not None:
                if _content_text(payload.get("content")):
                    session.current.observe_agent(at, final=True)
            # Codex response items are mirrored by richer item lifecycle events.
            # Counting both inflates calls because their identifiers can differ.
            # The event records below are the canonical execution source.
            continue

        if record_type != "event_msg":
            continue
        event_type = str(payload.get("type", "")).casefold()
        if event_type == "token_count":
            session.update_cumulative_usage(_find_cumulative_usage(payload))
            continue
        if event_type in {"user_message", "user_input"}:
            text = payload.get("message") or payload.get("text")
            if isinstance(text, str):
                session.add_prompt(text, at)
        elif event_type == "task_complete" and session.current is not None:
            session.current.observe_agent(at, final=True)
            duration = payload.get("duration_ms")
            if isinstance(duration, (int, float)):
                session.current.explicit_duration_ms = int(duration)
        elif event_type in {"agent_message", "assistant_message"} and session.current is not None:
            session.current.observe_agent(at, final=True)
        elif event_type in {"item_started", "item_completed", "item_failed"} and session.current is not None:
            item = payload.get("item")
            item = item if isinstance(item, dict) else payload
            name = item.get("name") or item.get("type") or "other"
            item_kind = str(item.get("type", "")).casefold().replace("_", "")
            if item_kind not in {
                "commandexecution",
                "filechange",
                "mcptoolcall",
                "collabagenttoolcall",
                "extension",
                "computertoolcall",
            }:
                continue
            command = _command_from(item)
            call_id = item.get("call_id") or item.get("id")
            signature = session.current.add_tool(name, at, call_id=call_id, command=command, inputs=item)
            if event_type != "item_started":
                session.current.finish_tool(
                    signature,
                    at,
                    exit_code=_structured_exit_code(item),
                    is_error=True if event_type == "item_failed" else _structured_is_error(item),
                    status=item.get("status") or ("failed" if event_type == "item_failed" else "completed"),
                    command=command,
                    name=name,
                    result_data=item,
                )
    output: list[dict[str, Any]] = []
    for session in sessions.values():
        output.extend(session.normalized())
    return output


def parse_claude(
    path: Path, source_file_hash: str, workspace_root: Path, diagnostics: dict[str, int]
) -> list[dict[str, Any]]:
    sessions: dict[str, SessionWork] = {}
    for record in iter_jsonl(path, diagnostics):
        session = _session(
            sessions,
            record.get("sessionId") or source_file_hash,
            source="claude",
            source_file_hash=source_file_hash,
            workspace_root=workspace_root,
        )
        cwd = record.get("cwd")
        if isinstance(cwd, str):
            session.cwds.add(cwd)
        at = _timestamp(record.get("timestamp"))
        record_type = str(record.get("type", "")).casefold()
        message = record.get("message")
        message = message if isinstance(message, dict) else {}
        role = str(message.get("role", "")).casefold()
        content = message.get("content")
        if record_type == "user" and role in {"", "user"} and _is_human_content(content):
            session.add_prompt(_content_text(content), at)
            continue
        if record_type == "assistant" or role == "assistant":
            if message.get("model"):
                session.current_model = str(message.get("model"))
                if session.current is not None:
                    session.current.model = session.current_model
            if session.current is None:
                continue
            if _content_text(content):
                session.current.observe_agent(at, final=message.get("stop_reason") == "end_turn")
            usage = _usage_values(message.get("usage"))
            session.current.add_summed_usage(usage, record.get("uuid") or message.get("id"))
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    inputs = block.get("input")
                    session.current.add_tool(
                        block.get("name"),
                        at,
                        call_id=block.get("id"),
                        command=_command_from(inputs),
                        inputs=inputs,
                    )
            continue
        if record_type == "user" and isinstance(content, list) and session.current is not None:
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                session.current.finish_tool(
                    block.get("tool_use_id"),
                    at,
                    is_error=block.get("is_error"),
                    exit_code=_structured_exit_code(record.get("toolUseResult")),
                    result_data=record.get("toolUseResult") or block,
                )
            continue
        if record_type == "result" and session.current is not None:
            is_error = bool(record.get("is_error")) or str(record.get("subtype", "")).casefold() in {
                "error",
                "failure",
            }
            if is_error:
                session.current.error_count += 1
            session.current.observe_agent(at, final=True)
        if session.current is not None:
            tool_result = record.get("toolUseResult")
            if isinstance(tool_result, dict):
                session.current.finish_tool(
                    tool_result.get("tool_use_id") or record.get("toolUseId"),
                    at,
                    is_error=tool_result.get("is_error"),
                    exit_code=_structured_exit_code(tool_result),
                    result_data=tool_result,
                )
    output: list[dict[str, Any]] = []
    for session in sessions.values():
        output.extend(session.normalized())
    return output


def parse_source_file(
    path: Path,
    source: str,
    source_file_hash: str,
    workspace_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    diagnostics: dict[str, int] = {"parse_errors": 0}
    if source == "codex":
        episodes = parse_codex(path, source_file_hash, workspace_root, diagnostics)
    elif source == "claude":
        episodes = parse_claude(path, source_file_hash, workspace_root, diagnostics)
    else:
        raise ValueError(f"Unknown source: {source}")
    diagnostics["episodes"] = len(episodes)
    return episodes, diagnostics
