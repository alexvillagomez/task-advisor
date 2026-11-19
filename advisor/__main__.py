"""Command-line entry point for training, advice, web, and MCP modes."""

from __future__ import annotations

import sys



def _pretty(result: dict[str, object]) -> str:
    complexity = result["complexity"]
    recommendation = result["recommendation"]
    confidence = result["confidence"]
    tiers = result["tiers"]
    lines = [
        f"Advice {result['advice_id']}",
        f"Complexity: {complexity['score']}/100 ({complexity['label']})",
        "Why: " + "; ".join(complexity["reasons"]),
        f"Recommend: {recommendation['model_tier']} tier, {recommendation['reasoning_effort']} reasoning "
        f"({recommendation['predicted_pass_chance']:.1%} pass, ~${recommendation['rough_cost_usd']:.6f})",
        f"Agent setup: {result['agent_setup']}",
        f"Verification: {result['verification']}",
        f"Confidence: {confidence['flag']} (nearest family: {confidence['nearest_training_family'] or 'none'})",
        "Tiers: " + " | ".join(
            f"{item['model_tier']} {item['predicted_pass_chance']:.1%} ~${item['rough_cost_usd']:.6f}"
            for item in tiers
        ),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = argv[0] if argv else None
    if mode in {"hook", "statusline"}:
        kind = argv[1] if mode == "hook" and len(argv)>1 else "statusline"
        if kind not in {"user-prompt", "stop", "statusline"}: return 0
        import os
        import threading
        timer = threading.Timer(.18, lambda: os._exit(0)) if kind == "user-prompt" else None
        if timer:
            timer.daemon = True
            timer.start()
        try:
            from .claude_code import hook_main
            return hook_main(kind, worker=True)
        except Exception: return 0
        finally:
            if timer: timer.cancel()
    if mode == "claude-code":
        from .claude_code import install_main
        try: return install_main(argv[1:])
        except (OSError, ValueError):
            print("Advisor settings update failed; check executable, JSON and permissions.", file=sys.stderr)
            return 1
    if mode == "--version":
        print("task-advisor 0.3.2")
        return 0
    if mode in {"train-baseline", "config", "chat", "backlog", "export-update", "merge-updates", "use-prior", "eval", "forget"}:
        from .cli import run
        try:
            return run(argv)
        except (OSError, ValueError) as exc:
            print("Advisor command failed: " + type(exc).__name__ + ". Check arguments, local files and permissions.", file=sys.stderr)
            return 1
    import argparse
    import json
    from pathlib import Path
    from .core import advise
    from .training import DEFAULT_ARTIFACT, DEFAULT_TRIALS, train
    if mode == "train":
        parser = argparse.ArgumentParser(prog="python -m advisor train")
        parser.add_argument("train")
        parser.add_argument("--trials", type=Path, default=DEFAULT_TRIALS)
        parser.add_argument("--output", type=Path, default=DEFAULT_ARTIFACT)
        args = parser.parse_args(argv)
        artifact = train(args.trials, args.output)
        print(f"Trained advisor on {artifact['training_rows']} rows -> {args.output}")
        return 0
    if mode == "serve":
        parser = argparse.ArgumentParser(prog="python -m advisor serve")
        parser.add_argument("serve")
        parser.add_argument("--port", type=int, default=8765)
        args = parser.parse_args(argv)
        from .web import serve

        serve(args.port)
        return 0
    if mode == "mcp":
        from .mcp import run_mcp

        run_mcp()
        return 0

    parser = argparse.ArgumentParser(prog="advisor", description="Local personal model router", epilog="Commands: chat, backlog, serve, eval, export-update, merge-updates, use-prior, forget, train, mcp. Use advisor COMMAND --help for options.")
    parser.add_argument("task_text")
    parser.add_argument("--stakes", choices=("low", "medium", "high"), default="medium")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = advise(args.task_text, args.stakes)
    print(json.dumps(result, indent=2) if args.json else _pretty(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

