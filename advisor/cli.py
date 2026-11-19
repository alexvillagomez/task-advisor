"""v0.2 command dispatch, preserving the original advice/train/MCP commands."""
import argparse
import json
import os
from pathlib import Path


def run(argv):
    mode = argv[0]
    parser = argparse.ArgumentParser(prog="advisor " + mode)
    if mode == "chat":
        parser.add_argument("--engine", choices=("codex", "claude"), required=True)
        parser.add_argument("--stakes", choices=("low", "medium", "high"), default="medium")
        parser.add_argument("--cwd")
        parser.add_argument("--mode",choices=("auto","collect","route"),default="route")
        parser.add_argument("--collect-target",type=int)
        parser.add_argument("--shadow",action="store_true")
        parser.add_argument("--shadow-high",action="store_true")
        parser.add_argument("--objective",choices=("ev","threshold"),default="threshold")
        parser.add_argument("--exploration", type=float, default=float(os.environ.get("TASK_ADVISOR_EXPLORATION", "0")))
        args = parser.parse_args(argv[1:])
        if not 0 <= args.exploration <= 1: parser.error("exploration must be between 0 and 1")
        from .chat import chat
        return chat(**vars(args))
    if mode == "backlog":
        parser.add_argument("--codex", action="store_true")
        parser.add_argument("--claude", action="store_true")
        parser.add_argument("--chatgpt-export", type=Path)
        parser.add_argument("--since")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--report", action="store_true")
        args = parser.parse_args(argv[1:])
        from .backlog import import_backlog, report
        from .storage import events
        if args.report:
            print(json.dumps(report(events()), indent=2)); return 0
        if not (args.codex or args.claude or args.chatgpt_export): parser.error("select --codex, --claude and/or --chatgpt-export")
        values = vars(args); values.pop("report")
        result = import_backlog(**values)
        print(json.dumps(result, indent=2))
        if not args.dry_run: print("Optional calibration: advisor serve shows 20 past feature summaries. No text is retained; you may skip this pass.")
        return 1 if result["failed_files"] else 0
    from .priors import export_update, merge_updates, use_prior, evaluate
    if mode == "train-baseline":
        parser.add_argument("--train-fraction",type=float,default=.8)
        args = parser.parse_args(argv[1:])
        from .baseline import train_baseline
        print(train_baseline(args.train_fraction))
    elif mode == "config":
        parser.add_argument("--init",action="store_true")
        args = parser.parse_args(argv[1:])
        from .config import config, DEFAULTS
        from .storage import state_dir
        path = state_dir()/"config.json"
        if args.init and not path.exists():
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(DEFAULTS,indent=2),encoding="utf-8")
        print(json.dumps(config(),indent=2)); print(path)
    elif mode == "export-update":
        parser.add_argument("--output", type=Path)
        parser.add_argument("--noise", type=float, default=0.)
        args = parser.parse_args(argv[1:]); print(export_update(args.output, args.noise))
    elif mode == "merge-updates":
        parser.add_argument("directory", type=Path)
        parser.add_argument("--output", type=Path)
        args = parser.parse_args(argv[1:]); print(merge_updates(args.directory, args.output))
    elif mode == "use-prior":
        parser.add_argument("file", type=Path)
        args = parser.parse_args(argv[1:]); use_prior(args.file); print("Team prior installed; personal weights rebuilt.")
    elif mode == "eval":
        parser.add_argument("--export", nargs="?", const=True, default=False)
        args = parser.parse_args(argv[1:]); print(json.dumps(evaluate(args.export), indent=2))
    elif mode == "forget":
        parser.parse_args(argv[1:])
        from .storage import state_dir
        directory = state_dir().resolve()
        # Recognize custom-named advisor exports without deleting unrelated files.
        # Resolve every target and never follow links out of the data directory.
        for path in directory.rglob("*.json") if directory.exists() else []:
            resolved = path.resolve()
            if not resolved.is_relative_to(directory) or path.is_symlink(): continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                owned = isinstance(payload, dict) and (
                    set(payload) == {"version", "deltas", "counts"} and payload.get("version") == "advisor-weights-v2"
                    or set(payload) in ({"evaluation", "tiers", "observed_total_spend_usd"},{"evaluation", "tiers", "observed_total_spend_usd","heldout"}) and set(payload.get("tiers", {})) == {"personal", "team", "baseline"}
                    or payload.get("version")=="advisor-baseline-v3")
                if owned: path.unlink()
            except (ValueError, OSError): pass
        # Delete only advisor-owned files, never recursively delete a user-selected
        # home or repository if TASK_ADVISOR_HOME was configured incorrectly.
        for name in ("advisor.sqlite3", "advisor.sqlite3-journal", "advisor.sqlite3-wal", "advisor.sqlite3-shm", "update.json", "eval.json", "team-prior.json", "advice_context.jsonl", "feedback.jsonl"):
            (directory/name).unlink(missing_ok=True)
        legacy = Path(__file__).with_name("state")
        for name in ("advice_context.jsonl", "feedback.jsonl"):
            (legacy/name).unlink(missing_ok=True)
        if directory.exists() and not any(directory.iterdir()): directory.rmdir()
        print("Advisor state forgotten. Separately remove exports saved elsewhere and engine-owned histories.")
    return 0
