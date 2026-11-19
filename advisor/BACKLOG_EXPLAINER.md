# Backlog bootstrap and the ChatGPT connection

Research date: **2026-11-19**. This explains the v0.2 contract in [technical document](../docs/ADVISOR_TECHNICAL.md), grounded in the existing readers, parsers, and outcome rules. At inspection, `advisor/__main__.py` still exposed v0.1 commands; backlog, chat, eval, and the strict feature-only schema were **under construction**, not verified shipping behavior. Proposed details below are explicitly identified. No private transcripts were opened for this research.

## 1. Why import a backlog?

The shipped baseline learns from synthetic, graded tasks. Before the first live thumb, history can tell a personal selector which model/effort combinations tend to work for this user's task mix. Each historical human turn becomes one observation of the model actually used, with locally computed task features and an imperfect outcome. This trains a small success predictor/router, not the underlying language model.

Weak labels offer directional evidence without requiring retrospective grading. A final passing test is stronger evidence than a clean completion; a retry or correction is evidence of trouble. Neither silence nor finishing an answer proves correctness. History also reflects selection bias: harder work may already have gone to stronger models. It cannot establish what an unchosen model would have done.

The existing [outcome combiner](../ingest/outcomes.py), version `ingest-outcome-v1.0.0`, applies this precedence:

| First matching condition | Tier | Score |
|---|---|---:|
| Final check failed | `verified_failure` | .05 |
| Correction/complaint or retry | `likely_failure` | .20 |
| Interrupted without final output | `likely_failure` | .25 |
| Final check passed | `verified_success` | .95; 1.0 after fail-to-pass |
| Approval | `likely_success` | .80 |
| Final output without tool errors | `likely_success` | .65 |
| Otherwise | `unclear` | .50 |

“Verified” here describes a recognized execution check, not universal verification of the whole task. Preserve components so labels remain auditable. ChatGPT's structure-only labels must not inherit the CLI clean-completion success rule.

The brief mandates down-weighting but specifies no multiplier. **Proposed starting policy:** explicit live thumbs weight 1.0; backlog verified rows .25; likely rows .10; unclear rows 0 for reward learning, retained for coverage. These are tuning choices, not implemented constants or measured optima. Label confidence and training weight are distinct. A live re-rating supersedes the earlier label; it must not create a second independent observation.

## 2. Where history lives and what is read

Paths are defaults, not promises that every client version writes identical files. `~` means the OS user's home. Windows WSL histories live in the Linux home, separate from native Windows histories.

| Source | Windows | macOS | Format |
|---|---|---|---|
| Codex CLI/app local sessions | `%USERPROFILE%\.codex\sessions\` and `archived_sessions\` | `~/.codex/sessions/` and `archived_sessions/` | Recursively discovered JSONL, including `.zst` streams |
| Claude Code | `%USERPROFILE%\.claude\projects\<project>\<session>.jsonl` | `~/.claude/projects/<project>/<session>.jsonl` | JSONL, recursively scanned |
| ChatGPT | User-selected ZIP, usually `%USERPROFILE%\Downloads\` | User-selected ZIP, usually `~/Downloads/` | Export archive; expected member `conversations.json` |
| Claude.ai | User-selected downloaded archive, usually Downloads | Same | Conversation/account export; see schema caveat below |

Codex locations come from this repository's [ingest README](../ingest/README.md). App history is covered only when materialized in these local roots; no cloud-only completeness claim follows. The current discovery code uses fixed home paths: do not assume it honors `CODEX_HOME` or custom configuration roots. Anthropic documents Claude Code's nested transcript layout and `CLAUDE_CONFIG_DIR` override; the existing ingest discovery does not honor that override either. [Claude directory reference](https://code.claude.com/docs/en/claude-directory), [settings reference](https://code.claude.com/docs/en/settings).

### Codex

[readers.py](../ingest/readers.py) streams UTF-8 JSONL, skips malformed/non-object records with diagnostics, and decompresses Zstandard in memory using optional `zstandard` or local `libzstd`. It does not extract a decompressed transcript.

[parsers.py](../ingest/parsers.py) reads envelope `timestamp`, `type`, and `payload`:

* `session_meta.payload.id`, session/thread identifiers: grouping only; `cwd` and repository metadata support exclusions.
* `turn_context.payload.model` and `.effort`: attributed to the current turn, retaining prior values when absent.
* `response_item` messages with `role=user`, or `event_msg` payload types `user_message`/`user_input`: human-turn boundaries and in-memory feature input. Mirrored prompts are deduplicated.
* Assistant messages, `agent_message`/`assistant_message`, and `task_complete`: timing/completion; `duration_ms` is a fallback.
* `token_usage_record`, `token_count`, and `event_msg/token_count`: cumulative usage, often under `info.total_token_usage`. Per-turn usage subtracts the baseline counters, clamped nonnegative.
* `event_msg/item_started|item_completed|item_failed`: recognized command, file-change, MCP, collaboration, extension, and computer items. IDs deduplicate calls; structured exit codes/status/error fields and command/result inspection produce tool counts and test/check outcomes. Mirrored response items are deliberately not counted again.

Token aliases normalize input/prompt, cached input, output/completion, and reasoning/thinking counters. Missing source usage is not evidence of zero consumption, although the legacy normalization can emit zeros. Likewise unsupported lifecycle records mean unavailable evidence, not successful tools. Drop message bodies, reasoning, tool arguments/output, commands, diffs, paths, repository URLs, and original identifiers from advisor persistence.

### Claude Code

Read `sessionId`, `timestamp`, `type`, `message.role/content/model/usage/stop_reason`, and `uuid`/message ID for deduplication. Human `user` content starts turns; `tool_result` blocks are not human prompts. Assistant `tool_use` blocks provide name, ID, and input; user tool results provide `tool_use_id`, `is_error`, and `toolUseResult` structured exit/result data. `result` records contribute final/error signals. Usage is summed across deduplicated assistant records, including cache-read/cache-creation counts in normalized cached input. This parser does **not** recover Claude reasoning effort: store unknown, rather than infer it from model names.

Discard content blocks, thinking, command strings, outputs, filesystem metadata, and original IDs after deriving features/outcomes. Available evidence includes tests, failures, retries, corrections, approval, tokens, and elapsed time. Nested subagent transcripts can distort counts if treated as independent founder turns; identify or exclude them rather than claiming every recursive JSONL row is a personal interaction.

### ChatGPT export

There is no supported local transcript directory to scrape. Official exports are ZIPs containing conversation JSON and other account data. Current documentation describes `index.html`; earlier exports may have `chat.html`. The importer contract specifically targets `conversations.json`, not either HTML viewer. Exact filenames/tree schemas are **not guaranteed by the official export article**; unsupported layouts should report a format gap. [Official export instructions](https://help.openai.com/en/articles/7260999-exporting-your-chatgpt-history-and-data).

The brief's expected JSON shape is:

```text
conversation: {mapping: {node_id: {parent, children, message}}, current_node}
message: {author: {role}, content: {parts}, metadata: {model_slug}, create_time}
```

Walk parents from `current_node`, reverse into chronological order, and associate each human turn with its assistant response/model. Read text parts in memory, tolerating null messages, non-text parts, absent timestamps/model slugs, and broken links. Inspect sibling branches for regenerated assistant answers and edited user prompts; do not train every alternative as another independent successful turn. Branch structure is only a proxy: siblings do not prove the user's reason for branching. Preserve regeneration/edit counts and next-turn correction/retry signals; absent evidence remains `unclear`.

Use `metadata.model_slug` when present; it is not proof of a specific backend snapshot or effort setting. Token accounting, tool exit codes, tests, exact generation latency, hidden reasoning, and deleted/unretained conversations are not reliable inputs. Inter-message time includes user idle time. **Thumbs feedback inclusion is unverified:** the official article does not specify it, so neither promise its absence nor manufacture ratings from branches. Drop titles, account data, all message/attachment content, URLs, and node IDs. Read the ZIP member directly without extracting text to disk.

### Claude.ai export

Request **Settings → Privacy → Export data** on web/desktop; receive an emailed download link. Anthropic confirms conversation/account data and voice recordings, but excludes original uploaded files. [Official export documentation](https://support.claude.com/en/articles/9450526-export-your-claude-data).

Section 7 provides **no Claude.ai export flag/parser**. Do not pass this archive to `--claude` (Claude Code) or `--chatgpt-export`. A future adapter could target `conversations.json` with conversation `uuid`, `chat_messages`, message `sender`, `text`/`content`, and `created_at`; those candidate keys, archive layout, model attribution, branches, effort, and feedback availability remain **unverified against official documentation**. No fields are currently read/stored for this source. A validated adapter would derive correction/retry and timing proxies, discard account/voice/attachment content, and keep unknown model/usage values unknown.

## 3. Connecting to ChatGPT going forward

There is no documented public API for live reading of a person's consumer ChatGPT chat history. API conversation resources are application/API state, not permission to enumerate consumer chats. The comparison below concerns the consumer website; local Codex sessions are a separate supported surface.

| Option | Benefits and limits | Privacy / terms risk | Switch per prompt? |
|---|---|---|---|
| Periodic export import | Supported, broad historical snapshot; delayed and manual | Sensitive archive stays local; lowest integration risk | No |
| Browser extension reading DOM | Can observe visible turns promptly; brittle selectors, missing unloaded branches/metadata | Broad page access; extension can expose text. Automated extraction presents material terms risk | No supported router control; UI automation is brittle |
| MCP app/connector in ChatGPT | Can recommend and accept ratings through tool calls; not a history subscription | Server receives supplied arguments, not automatic full transcript access; minimize logging and scope | No control of host model picker |
| Own chat front end / Codex CLI | Own the request boundary and outcome collection; must preserve context across model changes | Prompts go to selected provider; engine may persist transcripts separately | **Yes**, among accessible supported models |
| OpenAI API front end | Explicit request model, usage counters, application-managed conversation | API credentials, billing, and provider data handling apply | **Yes**, request `model` selects it |

An MCP server sees what the model sends in tool arguments (plus documented protocol metadata); a widget can also invoke tools with explicit arguments. It cannot silently read all chats or install a model-routing hook. This follows the documented tool-input boundary, rather than a claim that apps receive no metadata. [OpenAI app reference](https://developers.openai.com/plugins/reference).

The extension risk follows OpenAI's restriction on automated/programmatic extraction; owning an account does not itself establish an exception. This is a product-risk assessment, not a determination for every extension. [Terms of Use](https://openai.com/policies/terms-of-use/).

Codex supports ChatGPT sign-in, but that authorizes Codex usage, not consumer-history retrieval. The intended advisor wrapper selects model/effort before each CLI call and resumes the session; installed CLI support and entitlements must be checked. API wrappers instead set `model` on each Responses request. Neither changes a separate consumer browser conversation's selected model. [Authentication](https://learn.chatgpt.com/docs/auth), [CLI reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli), [Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create).

## 4. Persistence boundary and exact proposed row

The brief fixes feature families but not serialized names, buckets, taxonomy, or label weights. The following is an **explicit proposed v0.2 schema**, not an assertion about the unfinished implementation. Optional unavailable values are `null`; no arbitrary strings or content-derived vocabulary is allowed.

```text
FeatureVector = {
  length_bucket: "short"|"medium"|"long",  // chars: <=280, <=1400, >1400
  code: bool, math: bool, multistep: bool, question: bool,
  category: "code"|"math"|"writing"|"research"|"planning"|"other",
  stakes: "low"|"medium"|"high",
  base_pass: {cheap: float, mid: float, frontier: float}, // each [0,1]
  turn_index: int  // >=0
}
LabelRow = {
  schema_version: string, label_version: string, event_id: string,
  source: "backlog"|"live", origin: "codex"|"claude_code"|"chatgpt",
  request_at: ISO8601|null, features: FeatureVector,
  model: catalog_id|null, effort: effort_enum|null,
  label: {
    tier: verified_success|verified_failure|likely_success|likely_failure|unclear,
    reward: float, confidence: float, training_weight: float,
    explicit_rating: "good"|"bad"|null,
    tags: list["v"|"o"|"u"|"w"|"p"|"s"|"c"],
    components: {
      final_check_passed: bool, final_check_failed: bool, fail_to_pass: bool,
      user_correction: bool, user_retry: bool, user_approval: bool,
      interrupted: bool, has_agent_final: bool, has_tool_errors: bool
    }
  },
  retry_similarity: float|null, regenerations: int|null, user_edits: int|null,
  tool_calls: int|null, tool_failures: int|null, test_passes: int|null,
  test_failures: int|null, exit_status: int|null, latency_ms: int|null,
  tokens: {input: int|null, cached_input: int|null,
           output: int|null, reasoning: int|null},
  cost_usd: float|null, cost_estimated: bool,
  manual_override: bool, propensity: float|null
}
```

Probability/reward/confidence/similarity ranges are [0,1]; counts/costs are nonnegative. Event IDs are opaque, never prompt hashes. Historical choice propensity is unknown. Missing historical stakes are a declared default (medium), not reconstructed truth. Category/flag extraction needs a versioned deterministic implementation.

Store under `~/.task-advisor/`, overridden by `TASK_ADVISOR_HOME`. Keep the idempotency manifest separate: file hash+mtime and processing/schema version, never a prompt hash. Reprocessing changed files must replace affected rows, not double their training weight. Hashes are local bookkeeping, not anonymization guarantees.

Never write prompt/response text, redacted text, text hashes, embeddings, n-grams, titles, paths, commands, outputs, reasoning, or attachment bodies into advisor state. Reuse ingest readers/parsers/outcomes **in memory**; do not call `ingest run` as the bootstrap: it writes redacted `prompt_text`, identifiers, and aftermath fields. Existing v0.1 feedback also writes `redacted_prompt` and `prompt_hash`; it does not meet this v0.2 promise. Engine-owned transcripts and downloaded archives already contain text, independently of advisor. Live prompts necessarily leave the machine for the engine's provider; backlog featurization needs no model call.

## 5. How much data is useful?

**Engineering estimate, not an observed result:** 20–30 informative historical turns per model/category may justify modest shrinkage from baseline; 50–100 mixed-outcome turns per model/category plus 20–50 live ratings is a more credible initial comparison. Three models across four categories implies roughly 600–1,200 historical turns at that second threshold. Correlated turns, uniform successes, unknown models, and weak labels reduce effective sample size. No count guarantees beating baseline; continue falling back in sparse categories.

`advisor eval` is specified to compare personal/team/baseline locally using log loss, pick-rated-good rate, and cost. For credible validation, freeze candidate weights, hold out later sessions, score the same observed model outcomes, and report coverage and session-level uncertainty. Do not evaluate on the backlog used for training or treat its weak labels as ground truth. Counterfactual policy comparisons need logged live propensities and overlap; historical exports lack them. A lower held-out loss plus prospective rated-good/cost improvement supports promotion. Aggregate export alone must contain no event rows. These evaluation safeguards are recommendations beyond the brief's minimum metrics.

## 6. Founder first run

1. Request ChatGPT data now: profile → **Settings → Data controls → Export data → Export → Confirm export**. Downloads can take up to seven days; the settings-export link expires after 24 hours. Save the ZIP privately, outside the repository. Self-service availability depends on workspace. [Official instructions](https://help.openai.com/en/articles/7260999-exporting-your-chatgpt-history-and-data).
2. Wait for the v0.2 worker's package and privacy checks. In this checkout, verify command availability first; v0.1 does not implement the following workflow. From the repository root, on Windows:

```powershell
python -m pip install .
advisor backlog --help
advisor chat --help
$env:TASK_ADVISOR_HOME = Join-Path $env:USERPROFILE '.task-advisor'
$chatgptZip = Join-Path $env:USERPROFILE 'Downloads\chatgpt-export.zip'
advisor backlog --codex --claude --chatgpt-export "$chatgptZip" --since 2026-01-01 --dry-run
advisor backlog --codex --claude --chatgpt-export "$chatgptZip" --since 2026-01-01
advisor backlog --report
```

3. Replace the filename/date as appropriate; omit sources you do not use. A macOS equivalent after installation is `advisor backlog --codex --claude --chatgpt-export "$HOME/Downloads/chatgpt-export.zip" --dry-run`, then repeat without `--dry-run`. Review model/tier counts, parse failures, unknowns, and unclear share. Repeat imports should add no duplicate weight. Do not extract the ZIP or run the old ingest pipeline for this workflow.
4. Start the dashboard and optionally review 20 historical **feature summaries**. With no saved text, memory-based calibration may be unreliable; skipping it is valid. In separate terminals:

```powershell
advisor serve
codex login
advisor chat --engine codex
```

Open `http://127.0.0.1:8765`. Rate `+`/`-`, Enter to skip; use `/model`, `/stakes`, `/why`, `/rate`, `/quit` as needed. Claude Code users can choose `advisor chat --engine claude` after authenticating that CLI.

5. After collecting live ratings, run `advisor eval`; use `advisor eval --export` only for aggregate sharing. Periodically repeat backlog import for new exports. `advisor forget` is the specified local advisor reset; it does not delete provider history or the original archives. These commands are readiness instructions, not claims that installation or a real provider turn was exercised during this documentation task.
