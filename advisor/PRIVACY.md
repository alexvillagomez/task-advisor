# Privacy boundary and schema version 3

Advisor routes, featurizes, trains and evaluates locally. It has no telemetry or
uploader. Chat launches an installed engine CLI: prompts go to that provider.
Codex/Claude can retain their own transcripts, tools and history. Primary tools
can edit files. Advisor's no-text guarantee covers advisor-owned state and exports,
not provider histories, task outputs, swap, terminal scrollback, backups or engine logs.
Prompts, answers and session IDs exist only in advisor process memory. The preceding
prompt is held in memory for follow-up detection. No credentials or auth.json are read.

Default state is `~/.task-advisor/advisor.sqlite3`; `TASK_ADVISOR_HOME` overrides it,
then deprecated `ADVISOR_STATE_DIR`. SQLite secure deletion is enabled. Unknown
values are null or omitted, not fabricated zeroes. Validation reconstructs each row
from the allowlist before writing. No raw/redacted prompt, response, prompt/response
hash, embedding, filename, path, command, arbitrary model ID, free-text tag or tool
output is stored. Input text hashes in the hashing-trick clusterer exist in RAM only;
only the resulting integer cluster ID and fixed backend enum reach storage.

## Event envelope

| Field | Type / allowed values |
|---|---|
| id | random UUID hex; backlog deterministic UUID from source identity/position |
| schema | integer 3 |
| time | ISO timestamp or null for unavailable backlog time |
| source | live, backlog |
| engine | codex, claude, chatgpt |
| model | installed catalog ID or unknown |
| effort | none, low, medium, high, xhigh, max, unknown |
| propensity, predicted | nullable number in [0,1]; new randomized propensities >0 |
| rating | null, 0, 1 |
| tags | unique letters v/o/u/w/p/s/c |
| label | version enum, reward [0,1], source explicit/weak/unclear, confidence [0,1] |
| features, indicators | exact fields below |

## Full feature schema

All v3 additions are nullable, including migrated v2 rows. The original fields stay
required. Counts are nonnegative integers. Durations are finite nonnegative seconds.

| Fields | Type / meaning |
|---|---|
| length_bucket | short (<240 chars), medium (<1400), long |
| code, math, multistep, question | original booleans |
| category | code/math/research/writing/general |
| stakes | low/medium/high |
| base_predictions | cheap/mid/frontier numeric probabilities in [0,1] |
| turn_index | nonnegative integer |
| chars, words, lines | integers; word count uses Unicode word matches |
| code_blocks, inline_code | integers; fenced pairs and inline backtick spans |
| file_paths, urls, numbers | integer mention counts; no values retained |
| list_items, question_marks, constraints | integers; constraints count must/exactly/only |
| imperative_start | boolean from bounded verb rule |
| language | en/other heuristic (ASCII word share), not a reliable language detector |
| output_format | none/json/table/code, first matching enum |
| previous_reference, correction_cue | booleans from bounded local rules |
| cluster_id | integer 0..63 |
| cluster_backend | hash/minilm |
| turns_since_switch, cumulative_tokens | integers; prior completed turn streak and sum of observed input+output tokens |
| seconds_since_prompt, session_age | nonnegative seconds measured monotonically |
| previous_model, previous_effort | catalog/unknown and effort enums; no raw engine strings |
| previous_label | numeric combined reward in [0,1] or null |
| is_git, has_tests, has_ci | booleans; names/paths never retained |
| dirty_files | integer or null when git status unavailable |
| file_count_bucket | tiny (<20), small (<200), medium (<2000), large |
| project_size_bucket | tiny (<100KB), small (<10MB), medium (<1GB), large |
| top_extensions | list of up to three allowlisted extensions |
| local_hour, weekday, weekend | integers 0..23 / 0..6 (Monday=0), boolean |

Extension allowlist: .py .js .ts .tsx .jsx .go .rs .java .cs .cpp .c .rb .php
.swift .kt .sql .sh .ps1 .html .css .r .ipynb and other. Workspace traversal skips
symlinks, .git, node_modules, .venv and __pycache__, stops after a directory takes
the count to 10,000, and uses saturating buckets. Counts/size can be lower bounds
on very large projects. Git failures produce unknown dirty count. Context token
sums are usage totals, not an exact model context-window measurement after compaction.

Hash clustering uses 256 signed buckets, a seeded 32-dimensional random projection
and nearest normalized centroid. Optional cached MiniLM uses separate 384-dimensional
centroids fitted on the same synthetic prompts. Neither live embedding is stored.
Only synthetic centroid arrays ship in the package; no user centroid adaptation occurs.
The optional import uses local_files_only=True and never requests a model download.

## Outcomes and account signals

Numeric indicators: latency_ms, input_tokens, output_tokens, reasoning_tokens,
cached_tokens, cache_creation_tokens, cost_usd, exit_status, tool_calls, tool_failures,
command_failures, tests_run, tests_passed, tests_failed, retry_similarity, outcome_score,
quota_impact. Except exit_status, these must be finite and nonnegative.
Boolean indicators: retry, correction, manual_override, regeneration, user_edit,
cost_estimated, shadow, collection, quota_estimated. `outcome_tier` is one of
verified_success, verified_failure, likely_success, likely_failure, unclear.
`context_mode` is fresh/resumed/fresh_replay. Current shadows always record fresh.
`selection_probabilities` maps allowlisted catalog action keys to positive numeric
probabilities summing to one. It records collection support for off-policy evaluation.

`account` is a validated object with these fields:

| Field | Type / meaning |
|---|---|
| plan_type | free/plus/pro/team/business/enterprise/edu/unknown |
| limit_id | codex/claude/catalog Codex model/unknown |
| windows | primary and/or secondary objects |
| windows.*.quota_used_pct | finite number 0..100 |
| windows.*.window_minutes | nonnegative finite number |
| windows.*.resets_at | nonnegative numeric Unix timestamp |
| windows.*.minutes_to_reset | nonnegative finite minutes at observation |
| has_credits, unlimited, limit_reached | booleans; credits flags absent if unknown |
| credit_balance | nonnegative number, accepting a numeric source string |

Codex account telemetry comes from rate_limits in the JSON stream or a UUID-matched
thread rollout under ~/.codex/sessions. Only records timestamped after the current
invocation begins supply account updates. Earlier cumulative token totals are read
in RAM to subtract the pre-invocation total and recover usage across tool cycles. The raw reached-type string is reduced
to a boolean, and unrecognized plan/limit identifiers become unknown. No raw account
payload is written. Historical reset durations remain fixed; displays compute current
remaining time and omit expired windows. Quota slopes use positive deltas in the same
window, excluding decreases and increments over 20 percentage points.

Claude parsing supports usage, cache read/create, thinking tokens, total_cost_usd,
cost_usd, and aggregate numeric modelUsage fields. The local Claude project histories
inspected on 2026-11-19 exposed usage but no remaining-quota signal. Only key NAMES
were printed during inspection. Missing remaining quota stays unknown. ModelUsage
keys and tool content never reach storage. Codex rollout fallback is read-only;
auth.json and credentials are never read by advisor.

No success is inferred solely from a successful process exit. Explicit ratings
dominate weak signals. Tests require structured exit evidence; absent detail is
not proof of passing. Backlog training confidence is reduced to one quarter.

## Schema migration, pair labels and artifacts

On database open, PRAGMA user_version moves to 3 transactionally. Each v2 event gains
nullable v3 features and schema=3; existing ratings and v2 personal/team vectors are
preserved. Dropped text cannot be reconstructed. The 13-coordinate personal residual
ABI remains; user-baseline and verification heads use a richer numeric vector.

`pairs(a,b,preference,propensity)` stores only two opaque event IDs, A preference
(null/0/0.5/1), and the joint selection probability. Pair labels do not manufacture
absolute pass labels. `/rate` can add an absolute rating independently.

Settings contain personal/team weights, counts, user baseline, verification head,
legacy advice contexts and feedback. A user baseline has version, cutoff timestamp,
training event IDs, action weights/counts and numeric example counts. The local copy
`user-baseline.json` contains the same fields; it is not a team export. Config stores
only whitelisted numerical presets and billing enum. Nothing is uploaded.

The backlog manifest retains hashed source-file identity, whole-file SHA-256, mtime,
since filter and opaque row IDs for idempotence. These are FILE fingerprints, never
hashes of individual prompts. Ingest redacted episodes/internal hashes remain in RAM.
Old v0.1 text-bearing state is never imported.

## Exports, retention and deletion

`export-update` fields remain exactly version/deltas/counts. Deltas contain 13 numeric
personal/team coordinates per catalog action, L2 clipped to 1, then optionally noised
with Gaussian standard deviation --noise and bounded to [-4,4]. Counts are effective
local contributions and are not noised. This is not formal differential privacy,
anonymity or a guarantee against inference about workloads. Team merging is an
explicit local count-weighted average. Personal baselines are not exported.

`eval --export` contains methodology, legacy per-tier aggregate metrics, observed
spend and heldout aggregates/cutoff. Later-turn metrics include log loss, Brier, ECE,
rated-good rate, spend, cost per success and observed randomized-action IPS/DR
estimates. There are no event rows, task features or identifiers in evaluation exports.
Estimates assume sufficient support and representative ratings; high stakes and
budget caps restrict support. Missing quota/cost should not be interpreted as zero
actual cost; engine-dollar values are estimates when marked.

No automatic expiry exists. Re-rating rebuilds weights deterministically. Stop active
processes before `advisor forget`; it removes the database/journal and recognized
weight/eval/user-baseline files inside the state directory, but retains config and
unrelated files. It does not follow symlinks outside that directory. Exports elsewhere,
engine histories and backups require separate deletion. Logical deletion does not
guarantee forensic erasure on SSDs/backups.

The dashboard binds to 127.0.0.1, checks Host/Origin on JSON writes, has no access
logging, external assets or browser persistence. It is not multi-user authentication.
State is protected by normal OS file/account permissions, not advisor encryption.
