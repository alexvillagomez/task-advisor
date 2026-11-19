import json
from pathlib import Path
import zipfile

from advisor.backlog import import_backlog, report
from advisor.priors import export_update
from advisor.storage import events
from advisor.tests.test_v2 import CANARY, assert_private, home


def jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")


def fixtures(root):
    codex = root/".codex/sessions/session.jsonl"
    jsonl(codex, [
        {"type": "session_meta", "timestamp": "2026-10-01T00:00:00Z", "payload": {"id": "session-1"}},
        {"type": "turn_context", "payload": {"model": "gpt-6-luna", "effort": "low"}},
        {"type": "event_msg", "timestamp": "2026-10-01T00:00:01Z", "payload": {"type": "user_message", "message": CANARY + " write code"}},
        {"type": "event_msg", "timestamp": "2026-10-01T00:00:02Z", "payload": {"type": "item_completed", "item": {"id": "a", "type": "command_execution", "command": "pytest", "exit_code": 0}}},
        {"type": "event_msg", "timestamp": "2026-10-01T00:00:03Z", "payload": {"type": "agent_message", "message": CANARY}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 10, "output_tokens": 5}}}},
    ])
    claude = root/".claude/projects/project/session.jsonl"
    jsonl(claude, [
        {"type": "user", "sessionId": "session-2", "timestamp": "2026-10-02T00:00:00Z", "message": {"role": "user", "content": CANARY}},
        {"type": "assistant", "sessionId": "session-2", "timestamp": "2026-10-02T00:00:01Z", "message": {"role": "assistant", "model": "claude-sonnet-5-5", "content": [{"type": "text", "text": CANARY}], "usage": {"input_tokens": 15, "output_tokens": 8}}},
        {"type": "user", "sessionId": "session-2", "timestamp": "2026-10-02T00:00:02Z", "message": {"role": "user", "content": "Wrong, try again " + CANARY}},
    ])
    return codex, claude


def chatgpt_fixture(root):
    def node(parent, role, text, time, children=()):
        return {"parent": parent, "children": list(children), "message": {"author": {"role": role}, "content": {"parts": [text, {"ignored": CANARY}]}, "create_time": time, "metadata": {"model_slug": "gpt-6.1-terra"}}}
    conversation = {"current_node": "a2", "mapping": {
        "u": node(None, "user", CANARY, 1780000000, ["a1", "a2"]),
        "a1": node("u", "assistant", CANARY + " discarded branch", 1780000001),
        "a2": node("u", "assistant", CANARY + " selected branch", 1780000002),
    }}
    path = root/"export.zip"
    with zipfile.ZipFile(path, "w") as archive: archive.writestr("conversations.json", json.dumps([conversation]))
    return path


def test_backlog_all_sources_private_and_idempotent(home, tmp_path):
    history = tmp_path/"history"; history.mkdir()
    fixtures(history)
    exported = chatgpt_fixture(history)
    dry = import_backlog(codex=True, claude=True, chatgpt_export=exported, home=history, dry_run=True)
    assert dry["turns"] == 4 and not home.exists()
    result = import_backlog(codex=True, claude=True, chatgpt_export=exported, home=history)
    assert result["turns"] == 4 and result["failed_files"] == 0
    rows = events()
    assert all(r["source"] == "backlog" for r in rows)
    codex = next(r for r in rows if r["engine"] == "codex")
    assert codex["indicators"]["tests_passed"] == 1
    assert codex["indicators"]["outcome_tier"] == "verified_success"
    assert codex["indicators"]["output_tokens"] == 5
    assert codex["indicators"]["latency_ms"] == 2000
    claude = next(r for r in rows if r["engine"] == "claude")
    assert claude["indicators"]["correction"]
    gpt = next(r for r in rows if r["engine"] == "chatgpt")
    assert gpt["indicators"]["regeneration"]
    assert gpt["indicators"]["outcome_tier"] == "likely_failure"
    again = import_backlog(codex=True, claude=True, chatgpt_export=exported, home=history)
    assert again["turns"] == 0 and again["skipped_files"] == 3
    assert len(events()) == 4
    assert report(rows)["unclear_share"] == .25
    export_update()
    assert_private(home)
    assert not (history/"conversations.json").exists()


def test_since_then_broader_import_and_append(home, tmp_path):
    history = tmp_path/"history"
    codex, _ = fixtures(history)
    assert import_backlog(codex=True, home=history, since="2026-10-02")["turns"] == 0
    assert import_backlog(codex=True, home=history)["turns"] == 1
    with codex.open("a") as f:
        f.write('\n'+json.dumps({"type": "event_msg", "timestamp": "2026-10-03T00:00:00Z", "payload": {"type": "user_message", "message": CANARY + " new turn"}}))
    import_backlog(codex=True, home=history)
    assert len(events()) == 2
    assert_private(home)


def test_missing_fields_export_and_bad_zip(home, tmp_path):
    path = tmp_path/"conversations.json"
    path.write_text(json.dumps([{}, {"mapping": {"a": {"message": None}}}]))
    assert import_backlog(chatgpt_export=path)["turns"] == 0
    bad = tmp_path/"bad.zip"; bad.write_bytes(b"bad")
    assert import_backlog(chatgpt_export=bad)["failed_files"] == 1


def test_archiving_history_does_not_duplicate_turns(home, tmp_path):
    history = tmp_path/"history"
    codex, _ = fixtures(history)
    import_backlog(codex=True, home=history)
    archived = history/".codex/archived_sessions/renamed.jsonl"
    archived.parent.mkdir(parents=True)
    codex.rename(archived)
    import_backlog(codex=True, home=history)
    assert len(events()) == 1


def test_missing_time_and_malformed_history(home, tmp_path):
    path = tmp_path/"conversations.json"
    path.write_text(json.dumps([{"current_node": "user", "mapping": {"user": {"message": {"author": {"role": "user"}, "content": {"parts": [CANARY]}}}}}]))
    assert import_backlog(chatgpt_export=path, since="2026-01-01")["turns"] == 0
    assert import_backlog(chatgpt_export=path)["turns"] == 1
    assert events()[0]["time"] is None
    assert_private(home)
    codex, _ = fixtures(tmp_path/"history")
    with codex.open("a") as handle: handle.write('\n{"incomplete":')
    assert import_backlog(codex=True, home=tmp_path/"history")["failed_files"] == 1


def test_zstd_reader_reused(home, tmp_path):
    import ctypes
    from ingest.readers import _zstd_library, ReaderError
    import pytest
    try: lib = _zstd_library()
    except ReaderError: pytest.skip("No system libzstd")
    history = tmp_path/"history"
    codex, _ = fixtures(history)
    raw = codex.read_bytes()
    lib.ZSTD_compressBound.argtypes = [ctypes.c_size_t]; lib.ZSTD_compressBound.restype = ctypes.c_size_t
    lib.ZSTD_compress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]; lib.ZSTD_compress.restype = ctypes.c_size_t
    buffer = ctypes.create_string_buffer(lib.ZSTD_compressBound(len(raw)))
    size = lib.ZSTD_compress(buffer, len(buffer), raw, len(raw), 1)
    compressed = history/".codex/archived_sessions/session.jsonl.zst"
    compressed.parent.mkdir(parents=True)
    compressed.write_bytes(buffer.raw[:size]); codex.unlink()
    result = import_backlog(codex=True, home=history)
    assert result["turns"] == 1 and result["failed_files"] == 0
    assert_private(home)
