# Personal task advisor 0.3.2

Install with `python -m pip install .`, then run `advisor backlog --codex --claude`
and `advisor chat --engine codex`. See [QUICKSTART](../QUICKSTART.md).

The default is deterministic route mode: choose the cheapest action meeting the
stakes target (.72/.88/.95), with a latency penalty and lower effort on ties.
`usage_bias` defaults to `"low"`; `"neutral"` disables threshold latency/tie bias.
Collection is opt-in with `--mode collect` (or `--mode auto` until 300 ratings).
`--exploration 0.1` enables route exploration. `advisor config --init` creates
editable settings. `--objective ev` enables expected-value routing; subscription
scarcity increases with used percentage and never discounts for an approaching reset.
The catalog contains only gpt-6-luna, gpt-6.1-terra, gpt-6.1-sol, gpt-6-astra
and Claude haiku/sonnet/opus aliases. Prices/capability transfer are cold-start
estimates; this package does not verify current provider pricing or model access.

`--shadow` adds a different random model after A, in a fresh read-only sandbox.
Use `/b` at the rating prompt to expand B; `a`, `b`, or `=` records a pairwise
preference. B uses fresh context because the CLI cannot resume an immutable
pre-A snapshot after A completes. This limitation is recorded, and comparisons
of context-dependent follow-ups should be skipped. Shadow is disabled at high
stakes unless `--shadow-high` is also supplied. Primary engine permissions are
unchanged. Engine-owned transcripts are outside advisor's privacy boundary.

`advisor train-baseline` fits action-specific logistic residuals on randomized
ratings with normalized inverse-propensity weights plus Bradley-Terry pair losses.
It saves a local baseline and reserves the last 20% of primary turns. `advisor eval`
reports later-turn shipped/user/personal log loss, Brier, ECE, good rate and cost,
plus exact IPS/DR estimates on rated randomized primary actions. Those estimates
can have high variance and assume observed ratings are representative.

The verification head uses tags, structured tests and pairwise losses after 30
labelled examples; earlier advice uses the existing rules. The localhost dashboard
shows collection progress, model/effort/category coverage, quota windows and ratings.

Runtime is Python 3.10+ stdlib only. If sentence-transformers and cached MiniLM
are available, clustering uses the shipped MiniLM centroids; otherwise a deterministic
hash/projection clusterer is used. No model download is initiated. Both sets of 64
centroids were fitted on 2,048 synthetic trial prompts, with no BenchLM data.
The original sparse predictor and 13-coordinate personal/team residual ABI remain.

All new features, schema migration, account signals, privacy limits and deletion
are documented in [PRIVACY](PRIVACY.md). No prompt, response, text hash or per-turn
embedding reaches advisor-owned disk. Unknown measurements remain unknown.

Compatibility commands remain: `advisor "task"`, `advisor train`, `advisor mcp`.
The MCP tools `advise_task` and `report_outcome` preserve their original tier API;
model/effort collection, EV routing and the verification head are chat features.

```toml
[mcp_servers.task_advisor]
command = "advisor"
args = ["mcp"]
```

Tests: `python -m pytest advisor/tests -q`. Build: `python advisor_build.py` and
`python -m pip wheel . -w dist --no-deps`. The source ZIP includes tests and the
technical document. The wheel contains synthetic artifacts and unchanged ingest
reader dependencies. Optional `.zst` backlog input needs libzstd or zstandard.

## Using it in Claude Code

Install the 0.3.2 wheel, then preview and install user-scope hooks:

```powershell
advisor claude-code install --dry-run
advisor claude-code install
# Optional status line (preserves an existing statusLine):
advisor claude-code install --statusline
advisor claude-code uninstall --dry-run
advisor claude-code uninstall
```

The installer resolves the absolute `advisor` executable on PATH, merges into
`~/.claude/settings.json`, and backs up changed files as
`settings.json.advisor-bak-<timestamp>`. Restart Claude Code after installation.
This integration targets local Claude Code sessions, including Code sessions in
its desktop app that honor user settings; it does not integrate ordinary Claude chat.
No installer was run against the real user settings during development.

`UserPromptSubmit` recommends haiku/sonnet/opus and effort using threshold routing,
with no exploration or provider call. It displays `systemMessage` without model
context injection. No hook can initiate a model change in the verified installed
2.1.292 output schema (even PreModelSwitch only allows/denies/asks about an existing
switch). Switch manually with `/model sonnet`, `/model haiku`, or `/model opus`.
Use Claude Code's effort controls for the recommended effort.

After an answer, submit ONLY `+` or `-`, optionally followed by tags:
`v` verification, `o` overkill, `u` underpowered, `w` wrong, `p` partial,
`s` slow, `c` costly. Examples: `-vw`, `+o`, `/rate -c`.
These prompts are blocked and recorded against the last completed turn in that
session, with no model request. `+` / `-` avoid slash-command name conflicts.
A rating with no completed turn is blocked with a local explanation.

`advisor hook user-prompt` and `advisor hook stop` consume hook JSON on stdin;
`advisor statusline` consumes status line JSON. Stop reads only this turn's
transcript range in memory, deduplicates streamed usage by API message ID, and
stores model/effort plus numeric tokens, cache, tools, failures and timestamp duration.
Missing measurements remain unknown. The optional status line supplies the actual
model and effort; otherwise the first recommendation may say `unknown` and later
turns use the last observed model. Ratings feed the next Stop rebuild.

A 180 ms watchdog bounds prompt-hook processing (under 300 ms including measured
local CLI startup); failures and timeouts exit 0 silently. Slow machines can skip a
recommendation or rating on timeout. Advisor stores no prompt, response, transcript
path or content hash. Claude Code retains its own histories independently.
Verification details: [technical document](../docs/ADVISOR_TECHNICAL.md).

