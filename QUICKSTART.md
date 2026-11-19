# Task advisor 0.3.2

Python 3.10+; stdlib-only runtime, offline installation, no build downloads.

```powershell
python -m pip install dist/task_advisor-0.3.2-py3-none-any.whl
advisor backlog --codex --claude
advisor chat --engine codex
```

From a source checkout or unpacked `task-advisor-0.3.2.zip`, use `python -m pip install .`.
Codex or Claude must already be installed and authenticated. Supported Codex models:
gpt-6-luna, gpt-6.1-terra, gpt-6.1-sol, gpt-6-astra; gpt-6.2 is excluded.
Claude uses haiku/sonnet/opus aliases. Run from your project or pass `--cwd DIR`.

Chat defaults to deterministic threshold routing: cheapest action meeting the stakes
P(pass) target (.72/.88/.95), including a latency penalty with `usage_bias: "low"`.
`--objective ev` enables expected-value routing; reset proximity never discounts quota.
Use `--mode collect` for explicit collection, or `--mode auto` to collect until 300
rated live primary turns. `--collect-target 500` changes that target.
Route exploration is off by default; `--exploration 0.1` opts in.

Rate A with `+` or `-`, optionally followed by tags; Enter skips. Tags: `v` verification
needed, `o` overkill, `u` underpowered, `w` wrong, `p` partial, `s` slow, `c` costly.
Example: `-vw`. Commands: `/model gpt-6.1-terra`, `/model auto`, `/stakes high`,
`/why`, `/rate`, `/quit`. Overrides persist until `/model auto`. High-stakes random
sampling never chooses the cheap tier; manual model selection remains authoritative.

```powershell
advisor chat --engine codex --mode collect --shadow
advisor chat --engine claude --shadow
```

Shadow runs a different random model in a fresh read-only sandbox after A. B starts
collapsed: type `/b` at the rating prompt to display it, then `a`, `b` or `=` to record
A preferred, B preferred or tie. Preference is not an absolute pass/fail rating.
Use `/rate` to subsequently add/change A's absolute rating. B has fresh context,
so skip pair ratings when earlier conversation is necessary. High-stakes shadow
requires both `--shadow` and `--shadow-high`. Codex uses `-s read-only`; Claude has
tools and MCP disabled, hooks disabled and plan permission mode.

```powershell
advisor config --init
advisor serve
advisor train-baseline
advisor eval
```

Edit the printed config path (`~/.task-advisor/config.json`) to change the collection
target, stakes value/failure costs, billing (`subscription` or `api`), latency price,
daily spend/quota caps and quota stop percentage. Defaults pause exploration at
$5 estimated API-equivalent daily spend, 20 estimated quota percentage points/day,
or 95% reported window use. Deterministic routing and manual choices can continue
after a cap. These are exploration caps, not provider billing enforcement. Actual
usage can exceed estimates; concurrent processes do not reserve a shared budget.
`--objective threshold` is the default cheapest-above-target policy.

Open http://127.0.0.1:8765 after `advisor serve`. It shows collection progress,
coverage, quota windows/reset times, spend, and recent feature summaries with rating
buttons. It refreshes every two seconds. No prompt or answer history is shown.

`train-baseline` uses the first 80% of primary turns and saves `user-baseline.json`
locally. The last 20%, plus subsequently collected turns, form the later-turn
evaluation set. `--train-fraction 1` trains on all current data; collect new turns
before evaluating. Pair partners stay with their training primary. Missing ratings
produce missing metrics. Retraining moves the split; keep a fixed baseline while
comparing successive evaluations.

Codex usage is parsed from JSON events or its thread rollout. The Claude histories
inspected expose token usage but no remaining-quota signal; unknown quota is shown
as unknown. Model prices and quota conversion before measured deltas are estimates.
Other clients using the same account can confound quota learning.

Backlog options: `--dry-run`, `--since 2026-01-01`, `--report`, and
`--chatgpt-export PATH.zip` (or `conversations.json`). ZIPs are read without extraction.
Compressed `.zst` history additionally needs system libzstd or optional zstandard.
Backlog supports weak personal learning; it has no randomized propensities and is
excluded from IPS baseline fitting. Skip the optional dashboard calibration pass
if feature summaries do not identify the task well enough.

Team sharing remains manual:

```powershell
advisor export-update --output updates/alice.json --noise 0.05
advisor merge-updates updates --output team.json
advisor use-prior team.json
advisor eval --export
```

Only numerical personal/team residuals and counts are exported. No uploader exists.
Use one current update per person; duplicates count twice. Clipping/noise is not a
formal differential privacy guarantee. Sharing residuals from different personal
baselines is a heuristic; use a common baseline for meaningful team aggregation.

Advisor stores fixed non-text features/outcomes, never prompt/response text, text
hashes or per-turn embeddings. Engine CLIs send prompts to providers and may retain
their own histories. See [PRIVACY](advisor/PRIVACY.md) and the
[technical document](docs/ADVISOR_TECHNICAL.pdf).

`TASK_ADVISOR_HOME` overrides `~/.task-advisor`; legacy `ADVISOR_STATE_DIR` still works.
Stop active processes, then run `advisor forget` to delete managed data. Config is
retained. Exports elsewhere and engine histories require separate deletion. Old
v0.1 text-bearing feedback is never imported; remove legacy files on upgrade.

Validation: `python -m pytest advisor/tests -q`. Tests use fake engines and synthetic
rollouts. Installation, backlog, training, evaluation and tests make no provider calls.

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
Verification details: [technical document](docs/ADVISOR_TECHNICAL.md).
