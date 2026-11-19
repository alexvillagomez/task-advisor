"""Actual subprocess fixture; never imports a provider client."""
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
prompt = sys.stdin.read()
assert prompt and prompt not in args
session = "7fd61018-a567-45db-88de-d298409a3503"
if "resume" in args: assert args[args.index("resume")+1] == session
if "--resume" in args: assert args[args.index("--resume")+1] == session
if os.environ.get("FAKE_ENGINE_ARGS"):
    with Path(os.environ["FAKE_ENGINE_ARGS"]).open("a") as f: f.write(json.dumps(args)+"\n")
if "-p" in args:
    print(json.dumps({"type": "result", "result": "Answer: " + prompt, "session_id": session, "usage": {"input_tokens": 20, "output_tokens": 10}, "total_cost_usd": .002, "is_error": False}))
else:
    for row in [{"type": "thread.started", "thread_id": session}, {"type": "item.completed", "item": {"id": "check", "type": "command_execution", "command": "python -m pytest", "exit_code": 0}}, {"type": "item.completed", "item": {"id": "answer", "type": "agent_message", "text": "Answer: " + prompt}}, {"type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 10}}]: print(json.dumps(row))
