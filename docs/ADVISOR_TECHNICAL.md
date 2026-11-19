# Advisor 0.3.2
# Personal model routing from your own evidence

**Technical design, statistical specification and operating limits**  
Version 0.3.2 / schema 3 / 19 November 2026

Advisor selects a model and reasoning effort, runs an installed Codex or Claude CLI,
collects structured outcomes and learns from local ratings. Default routing conserves usage with threshold selection. Opt-in collection builds
a baseline from the owner's work using randomized actions, known propensities
and visible coverage.

The central constraint is a content-free persistence boundary. Text is available in
RAM for inference, classification and forwarding to an engine. Advisor writes only
validated counts, enums, numerical weights, labels and usage. Provider/engine
transcripts are a separate boundary and may contain the original text.

<div class="note"><strong>Implementation contract.</strong> Runtime requires only Python 3.10+ standard library. Optional cached MiniLM adds a local semantic encoder. Training never uses BenchLM. Supported Codex models: gpt-6-luna, gpt-6.1-terra, gpt-6.1-sol and gpt-6-astra. Claude uses haiku/sonnet/opus aliases. Provider access and prices are account-specific; this package does not verify them.</div>

### Reading map

| Pages | Subject |
|---|---|
| 2-3 | Architecture, data flow and privacy boundaries |
| 4-6 | Full typed feature, context, outcome and account schema |
| 7-8 | Shipped sparse baseline, personal residual and label combiner |
| 9-10 | Expected value, quota pricing, scarcity and collection |
| 11-12 | Shadow comparisons, IPS fitting and verification head |
| 13-14 | Later-turn evaluation and persistent schema/migration |
| 15-16 | Team aggregation, training roadmap and operating limits |

**Notation.** x denotes pre-action features, a a model/effort action, y an absolute
pass label, q a logged selection probability and σ the logistic function. Every
implemented equation names its source file and function. Mathematical blocks use
native HTML typography, subscripts and superscripts; rendering requires no CDN.

<!-- page -->
## Architecture and data flow

<svg viewBox="0 0 680 690" role="img" aria-label="Advisor architecture and privacy boundary">
<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L7,3 L0,6" fill="#387b89"/></marker></defs>
<rect x="12" y="12" width="656" height="250" rx="14" fill="#f5f1e9" stroke="#cbb994"/>
<text x="30" y="39" font-size="15" fill="#6b552e">TEXT / ENGINE BOUNDARY: provider calls and engine-owned history</text>
<g fill="#fff" stroke="#a99370"><rect x="36" y="64" width="178" height="64" rx="7"/><rect x="258" y="64" width="174" height="64" rx="7"/><rect x="478" y="64" width="162" height="64" rx="7"/><rect x="258" y="166" width="174" height="64" rx="7"/></g>
<g font-size="14" fill="#384653" text-anchor="middle"><text x="125" y="88">Terminal chat</text><text x="125" y="110">prompt + rating</text><text x="345" y="88">Codex / Claude CLI</text><text x="345" y="110">primary + shadow</text><text x="559" y="88">Provider</text><text x="559" y="110">inference</text><text x="345" y="190">Session rollouts</text><text x="345" y="212">resume + usage</text></g>
<g stroke="#387b89" stroke-width="2" fill="none" marker-end="url(#arrow)"><path d="M214 88 H258"/><path d="M432 88 H478"/><path d="M345 128 V166"/><path d="M300 166 V137 H214"/></g>
<text x="35" y="183" font-size="12" fill="#6b552e">Prompt / answer / session ID</text><text x="35" y="203" font-size="12" fill="#6b552e">remain in advisor RAM.</text><text x="35" y="223" font-size="12" fill="#6b552e">Engine may persist transcripts.</text>
<rect x="12" y="284" width="656" height="390" rx="14" fill="#edf5f7" stroke="#6394a0"/>
<text x="30" y="310" font-size="15" fill="#20546a">ADVISOR STATE: NON-TEXT ONLY</text>
<g fill="#fff" stroke="#6394a0"><rect x="36" y="338" width="178" height="64" rx="7"/><rect x="258" y="338" width="174" height="64" rx="7"/><rect x="478" y="338" width="162" height="64" rx="7"/><rect x="36" y="450" width="178" height="64" rx="7"/><rect x="258" y="450" width="174" height="64" rx="7"/><rect x="478" y="450" width="162" height="64" rx="7"/><rect x="36" y="574" width="178" height="64" rx="7"/><rect x="258" y="574" width="174" height="64" rx="7"/><rect x="478" y="574" width="162" height="64" rx="7"/></g>
<g font-size="13" fill="#213e50" text-anchor="middle"><text x="125" y="363">Features + cluster ID</text><text x="125" y="384">counts / enums only</text><text x="345" y="363">Outcome + quota parser</text><text x="345" y="384">allowlisted telemetry</text><text x="559" y="363">Backlog importers</text><text x="559" y="384">Codex / Claude / ChatGPT</text><text x="125" y="475">EV / collection policy</text><text x="125" y="496">baseline + personal</text><text x="345" y="475">SQLite local store</text><text x="345" y="496">events / pairs / weights</text><text x="559" y="475">Localhost dashboard</text><text x="559" y="496">coverage / quota / ratings</text><text x="125" y="599">MCP compatibility API</text><text x="125" y="620">advice / outcome tools</text><text x="345" y="599">Training + evaluation</text><text x="345" y="620">IPS / BT / later turns</text><text x="559" y="599">Priors + exports</text><text x="559" y="620">manual team transfer</text></g>
<g stroke="#387b89" stroke-width="2" fill="none" marker-end="url(#arrow)"><path d="M125 128 V145 H24 V325 H125 V338"/><path d="M345 230 V338"/><path d="M432 198 H559 V338"/><path d="M125 402 V450"/><path d="M345 402 V450"/><path d="M559 402 V420 H400 V450"/><path d="M214 475 H258"/><path d="M478 475 H432"/><path d="M345 514 V574"/><path d="M125 574 V514"/><path d="M432 604 H478"/><path d="M559 574 V544 H387 V514"/></g>
</svg>

**Source map:** `chat.py:chat`, `engines.py:run`, `features.py:featurize`,
`usage.py:rollout_usage`, `backlog.py:import_backlog`, `storage.py:put`,
`web.py:dashboard_data`, `mcp.py:run_mcp`, `priors.py:export_update`.
Boundary-crossing arrows represent in-memory parsing followed by validation.
MCP preserves its original tier interface; chat owns model/effort collection and EV.

<!-- page -->
## Invocation, context and privacy

A primary turn begins with text, stakes and conversation metadata in memory. The
feature extractor produces a fixed schema. `policy.py:choose` reads numerical
history and returns an action, probability, cost estimate and propensity.
`engines.py:run` passes the prompt on standard input, not as a process argument.
The response is parsed in RAM; only the allowed event reaches `storage.py:put`.

The primary session ID stays in memory and is supplied to Codex exec resume or
Claude --resume. Model changes preserve that primary conversation. If an engine
returns no session ID, chat stops rather than silently losing context. The prior
prompt supports next-turn retry/correction detection and is replaced after a
completed turn. It is never saved by advisor.

### Two boundaries, different guarantees

| Surface | Behavior |
|---|---|
| Advisor SQLite, artifacts, exports | No prompt/response text, text hashes, per-turn embeddings, paths or commands |
| Engine process / provider | Receives prompts; normal provider/account terms apply |
| Engine history / operating system | May retain transcripts, context, scrollback, swap or backups |
| Primary tools | Normal CLI permissions; advisor never bypasses approvals |
| Shadow tools | Codex read-only; Claude tools/MCP/hooks disabled with plan permissions |
| Dashboard | 127.0.0.1 only; no external scripts, persistence or access logs |

The write boundary reconstructs events instead of serializing arbitrary engine
objects. Plans, extensions, model IDs, tags and context modes are enums. Raw limit
error strings become booleans. Unknown identifiers become `unknown`. Privacy tests
inspect the database and artifacts for private canary markers.

Backlog reads existing histories in place and holds ingest episodes in RAM. Its
manifest uses whole-file fingerprints and source-file identity for idempotence;
those are not prompt hashes. No `auth.json` or credential file is read. During
implementation, only usage key names from five recent files per engine were printed,
with no message bodies.

**Implementation:** `storage.py:clean_indicators`, `make_event`, `put`;
`features.py:validate`; `backlog.py:import_backlog`. The full allowlist and deletion
rules are in `advisor/PRIVACY.md`. This boundary is data minimization, not anonymity,
encryption or protection against inference from workload metadata.

<!-- page -->
## Typed prompt features and clusters

Prompt transformations occur locally. New fields can be null for legacy rows;
fresh prompts populate these fields. No token strings or user vocabulary features
are written with events.

| Field(s) | Type and definition |
|---|---|
| chars, words, lines | Integers: characters, Unicode word matches, newlines + 1 |
| code_blocks, inline_code | Integer fenced-pair and inline-backtick counts |
| file_paths, urls, numbers | Integer mention counts; matched values discarded |
| list_items, question_marks | Integer marker counts |
| imperative_start | Boolean from a bounded first-verb rule |
| language | en/other; en when ASCII-word share is at least .8 |
| constraints | Integer count of must/exactly/only |
| output_format | none/json/table/code; first matching requested enum |
| previous_reference, correction_cue | Booleans from bounded local rules |
| category | code/math/research/writing/general |
| cluster_id, cluster_backend | Integer 0..63 and hash/minilm |
| length_bucket | short <240 chars; medium <1400; long otherwise |
| code, math, multistep, question | Original booleans |
| stakes, base_predictions | low/medium/high; three numeric tier probabilities |

### Semantic cluster construction

<div class="math">h<sub>j</sub> = Σ<sub>w in prompt</sub> sign(w) · 1[bucket(w)=j]<br>z = normalize(hR), &nbsp; c = argmax<sub>k∈{0,…,63}</sub> z · μ<sub>k</sub><br><small>Source: clusters.py:hashed_vector, projection, nearest</small></div>

BLAKE2b provides transient signed hashing into 256 buckets. R has 32 columns of
seeded ±1/√32 entries (seed 61119). Spherical Lloyd updates normalize cluster sums;
empty clusters retain their centers. Initial centers use a deterministic sample.
The hash centroids use eight iterations on a reservoir of 2,048 unique synthetic
trial prompts from `model/out/trials.csv`.

Cached all-MiniLM-L6-v2 instead uses normalized 384-dimensional vectors and separately
fitted centroids (four iterations on the same prompts). Loading is local-files-only;
otherwise the hash path works with stdlib alone. `clusters.py:train_clusters` writes
synthetic centroids and provenance, never the prompt reservoir. Live vectors/hashes
stay in RAM; only the cluster ID and backend enum persist.

<!-- page -->
## Conversation, workspace and time

`features.py:featurize` receives context already known to chat; it never recovers
text from the database. `features.py:workspace` inspects the working directory and
emits only counts and enums. New fields are nullable; unavailable values stay unknown.

| Field | Type / interpretation |
|---|---|
| turn_index | Nonnegative primary-turn index, beginning at zero |
| turns_since_switch | Integer streak of completed turns on the previous model |
| seconds_since_prompt | Nullable nonnegative seconds between prompt arrivals |
| previous_model | Nullable catalog model or unknown |
| previous_effort | Nullable none/low/medium/high/xhigh/max/unknown |
| previous_label | Nullable combined reward in [0,1] |
| cumulative_tokens | Integer sum of observed primary input+output tokens |
| session_age | Nonnegative seconds since chat started |
| is_git, has_tests, has_ci | Nullable booleans from local detection |
| dirty_files | Nullable count from git porcelain status |
| file_count_bucket | tiny <20, small <200, medium <2000, large |
| project_size_bucket | tiny <100KB, small <10MB, medium <1GB, large |
| top_extensions | Nullable list of at most three allowed suffixes |
| local_hour, weekday, weekend | Integers 0..23 and 0..6 (Monday zero); boolean |

Extension values are restricted to `.py .js .ts .tsx .jsx .go .rs .java .cs .cpp .c
.rb .php .swift .kt .sql .sh .ps1 .html .css .r .ipynb` and `other`. No directory,
file or branch name persists. Tests/CI are flags for conventional locations. The
walker skips symlinks and .git/node_modules/.venv/__pycache__, and stops after a
directory brings the count to 10,000. Large-project size can be a lower bound.

### Numeric representation

The personal vector φ retains 13 coordinates: intercept, length bucket, four
booleans, five task-category indicators, stakes and clipped turn index. The user
baseline and verification heads use ψ: φ plus transformed new numbers with presence
flags, boolean/presence pairs, enum one-hots, 64 cluster indicators, previous
model/label and extension indicators.

<div class="math">numeric(v) = min(1, log(1+v)/12), &nbsp; present(v)=1[v≠null]<br>φ<sub>turn</sub> = min(turn_index,20)/20<br><small>Source: features.py:vector, rich_vector</small></div>

Missing numbers encode zero plus a separate absence flag. Unknown enums activate
no one-hot coordinate. These are coarse context features: cumulative usage is not
an exact provider context length after reuse or compaction. Workspace failures leave
dirty count unknown rather than interpreting an unavailable status as clean.

<!-- page -->
## Outcomes and account telemetry

Adapters accept JSON and JSONL. A zero process exit alone never establishes success.
Tests require structured tool identities and exit evidence; absent detail is not a
fabricated passing test. Raw tool outputs are discarded after classification. Collection events also store
selection_probabilities: allowed action keys and positive probabilities summing to one.

| Field group | Stored type and interpretation |
|---|---|
| input_tokens, output_tokens, reasoning_tokens | Nonnegative numbers; reasoning is not added again to output |
| cached_tokens, cache_creation_tokens | Nonnegative numbers when supplied |
| latency_ms, cost_usd, quota_impact | Nonnegative numbers; quota impact is estimated percentage points |
| exit_status | Finite numeric exit status |
| tool_calls, tool_failures, command_failures | Nonnegative structured counts |
| tests_run, tests_passed, tests_failed | Nonnegative check-command counts |
| retry_similarity, outcome_score | Nonnegative weak-signal numbers |
| retry, correction, manual_override, regeneration, user_edit | Booleans |
| cost_estimated, shadow, collection, quota_estimated | Boolean provenance flags |
| outcome_tier | verified_success/failure, likely_success/failure, unclear |
| context_mode | fresh/resumed/fresh_replay enum; current shadow uses fresh |

### Quota window object

`indicators.account` has `plan_type` (free/plus/pro/team/business/enterprise/edu/unknown),
`limit_id` (codex/claude/allowed Codex model/unknown), booleans `has_credits`,
`unlimited`, `limit_reached`, numeric `credit_balance`, and a `windows` object.
Each primary/secondary window can contain `quota_used_pct` (0..100), `window_minutes`,
`resets_at` (Unix seconds) and `minutes_to_reset`. Unknown credit fields are omitted.
Reset durations refresh for display without rewriting history; expired windows drop
out of current-state pricing.

`usage.py:extract` reads top-level or payload rate_limits. `rollout_usage` locates
a Codex JSONL file by UUID thread ID and accepts records timestamped after invocation
start for account updates. Cumulative token deltas across invocation boundaries
recover all tool cycles, with last_token_usage as fallback. Those key names were
observed in local Codex histories; no rollout record is written by advisor.

Claude parsing supports usage, cache read/create, thinking details, modelUsage
aggregates and total_cost_usd/cost_usd. The five local Claude project files inspected
exposed usage but no remaining-quota signal. No live provider response was requested
for validation. Missing remaining quota stays unknown; fallback pricing is a prior.

**Implementation:** `engines.py:parse_output`, `run`; `usage.py:rate_limits`,
`clean_account`; `quota.py:account_state`; `storage.py:clean_indicators`.

<!-- page -->
## Shipped baseline and evidence score

The existing sparse logistic artifact is trained on graded synthetic trials.
Runtime uses unique unigram/bigram features with numbers collapsed to one token.
Those transient features select fixed coefficients; user events retain only the
resulting three tier probabilities.

<div class="math">p<sub>learned</sub> = σ(b + Σ<sub>j∈active</sub> w<sub>j</sub> + w<sub>L</sub> log(1+words)/6 + w<sub>H</sub> log(1+lines)/3)<br>σ(z) = 1 / (1 + exp(-clip(z,-24,24)))<br><small>Source: core.py:_learned_probability; training.py:text_features, _sigmoid</small></div>

`training.py:train` defaults to six epochs and learning rate .035/√(epoch+1), with
intercept update η(y-p) and active coefficients updated by η[(y-p)x-.0008w]. The
vocabulary uses document frequency at least 8 and at most 90% of documents, capped
at 900 features. This routine is separate from the v0.3 user-baseline trainer.

### Rules, evidence and tier ordering

<div class="math">D = .45·code + .55·external + .50·ambiguity + .45·multistep + .40·long + min(.35,.035·numbers)<br>p<sub>adjusted,t</sub> = σ(logit(p<sub>learned,t</sub>) - m<sub>t</sub>D)<br>m<sub>cheap,mid,frontier</sub> = (1, .72, .48)<br><small>Source: core.py:_rule_penalty, advise</small></div>

Evidence E is min(1, maximum family-signature hit count / 3). E below .30 means
low evidence. External-fact and high-risk tasks force low evidence and cap E at .2.
Then the adjusted probability becomes .58p + .42b<sub>tier</sub> - .025, where b is the
synthetic configuration's observed base rate. Each later tier is forced to at least
the previous tier plus .025 before clipping to [.03,.98]. This is a monotonic prior,
not measured superiority on the user's tasks. `core.py:_evidence`, `advise`.

The compatibility complexity score is round(100[.72(1-p<sub>frontier</sub>) +
.28 min(1,D/2.1)]), with minima 36 for code, 42 external, 46 multistep, 50 long and
55 high risk (`core.py:advise`). Chat routes with threshold by default and optional EV rather
than treating complexity as the objective. Tier mappings and price snapshots ship
as JSON; randomized personal data addresses uncertainty in capability transfer.

<!-- page -->
## Personal residual and label combiner

Each action receives an effort offset, a personal residual and, once fitted, a
richer local-baseline residual on top of the shipped tier prediction.

<div class="math">P(pass | x,a) = σ(logit(p<sub>tier</sub>) + e<sub>a</sub> + θ<sub>a</sub>·ψ(x) + w<sub>a</sub>·φ(x))<br>e<sub>none,low,medium,high,xhigh,max</sub> = (-.08, 0, .12, .24, .30, .35)<br><small>Source: policy.py:predict; θ=0 before train-baseline</small></div>

`policy.py:learn` replays events in insertion order from the team prior. Explicit
ratings have confidence 1; backlog confidence is multiplied by .25. Unknown effort
shares strength equally among that model's efforts. Unmatched models do not update
an action. Re-rating rebuilds weights, avoiding double counting superseded labels.

<div class="math">η<sub>a</sub> = .25 / √(1 + .02 n<sub>a</sub>)<br>w<sub>a</sub> ← clip(w<sub>a</sub> + η<sub>a</sub>s[(y-p)φ - .002w<sub>a</sub>], -4,4)<br>n<sub>a</sub> ← n<sub>a</sub> + s<br><small>Source: policy.py:learn; s = confidence × source weight / matched efforts</small></div>

### Absolute label composition

| Evidence, in priority order | Reward / confidence |
|---|---|
| Explicit + or - | 1 or 0 / 1; overrides weak signals |
| Imported outcome tier | Imported score (default .5); .85 verified, .4 likely, .05 unclear |
| Nonzero process exit | Signal (0, 1) |
| Failed tests; else passed tests | (.05,.95); else (.95,.9) |
| Correction; retry | (.1,.8); (.2,.65) |
| Tool or command failure | (.25,.5) |
| No usable signal | .5 / .05, source unclear |

For local weak signals, reward y=Σv<sub>i</sub>c<sub>i</sub>/Σc<sub>i</sub> and confidence=max c<sub>i</sub>
(`labels.py:combine`). This is a heuristic, not an independent-evidence likelihood.
Preference is not converted into absolute correctness.

Tag o adds lower-tier target .95 with .35s strength. Tag u adds .95 for higher tiers
and .05 otherwise with .35s strength (`policy.py:learn`). These are weak counterfactual
hints, not verified outcomes. Tags v/w inform verification. Team residuals retain
their 13-coordinate ABI; the rich baseline remains local.

<!-- page -->
## Expected value and quota shadow price

Default threshold routing minimizes catalog cost + latency penalty for P(pass)
&gt;= .72/.88/.95 by stakes; if unmet, maximize P(pass). `usage_bias=low` adds latency
cost and lower effort on ties; neutral disables that bias. Route/exploration=0 are
defaults. Optional `--objective ev` uses (V,C): (.01,.01), (1,2), (10,50):

<div class="math">a* = argmax<sub>a</sub> [V·p<sub>a</sub> - C<sub>fail</sub>(1-p<sub>a</sub>) - cost(a) - λ<sub>lat</sub>L<sub>a</sub>]<br><small>Source: policy.py:choose; config.py:DEFAULTS</small></div>

API cost uses catalog input/output prices with length/effort token estimates.
Subscription cost uses predicted tokens, quota percentage per token and scarcity.
Observed model/effort token and latency means replace cold-start estimates. Default
output estimates are 300/600/1200 for low/medium/high and latency 5/15/40 seconds.
The token estimate includes prompt length, with a minimum of 60 input tokens.

<div class="math">r = max(1,100-u)<br>S(u) = (100/r)<sup>2</sup><br>cost<sub>subscription</sub>(a) = max<sub>windows j</sub> [T<sub>a</sub> &middot; κ<sub>model,j</sub> &middot; $<sub>pct</sub> &middot; S(u<sub>j</sub>)]<br><small>Source: quota.py:scarcity, price</small></div>

Scarcity is nondecreasing in used percentage and saturates below one remaining
percentage point. Time-to-reset never discounts cost. The maximum window price
approximates the binding constraint instead of charging the request twice. Missing
windows use Tκ<sub>prior</sub>$<sub>pct</sub>, defaulting to κ=.0001 percentage points/token and
$<sub>pct</sub>=.05 dollar-equivalent/point.

### Learning the quota conversion

`quota.py:slopes` computes κ=ΣΔused_pct/Σ(input_tokens+output_tokens) per engine,
model and window. Accepted increments are positive and at most 20, with matching
reset timestamps and nonzero tokens. Decreases and changed windows are excluded.
Other clients sharing the account can contribute to Δ; this is observational.

The pick line shows P(pass), estimated API/quota-value cost, percentage impact,
remaining quota and reset time. Unknown windows are labelled unknown. Estimated
API-equivalent cost is not a subscription invoice. `--objective threshold` is
the default cheapest-above-target policy with .72/.88/.95 targets (`policy.py:choose`).
High-stakes EV routing also excludes the cheap tier unless manually overridden.

<!-- page -->
## Collection, propensities and budgets

Opt-in `--mode auto`: collect until the target number of rated live primary turns,
then route. The target defaults to 300 and is editable through config or
--collect-target. Absolute ratings and pair preferences count; shadow turns do not
count as additional primary turns. `chat.py:chat`, `web.py:dashboard_data`.

### Stratified randomized selection

<div class="math">g(a,x) = 1 / √[(1+n<sub>a</sub>)(1+n<sub>a,category(x)</sub>)]<br>q(a|x) = g(a,x) / Σ<sub>b∈eligible</sub> g(b,x)<br><small>Source: policy.py:collection_distribution, choose</small></div>

Counts include previous live uses, shadows and unrated turns. Every eligible option
retains positive probability. A uniform draw over the cumulative q selects the
action; its exact q is logged. This is randomized inverse-coverage allocation,
not a deterministic round-robin schedule. Coverage spans model, effort and category.

The eligible set is engine-specific. High stakes remove cheap-tier random actions.
Remaining daily spend/quota budgets exclude options whose estimated next use will
not fit. Shadows exclude A's entire model, including other efforts. `/model X`
overrides sampling with conditional propensity 1 and a manual flag; forced turns
are excluded from IPS pass fitting.

### Budget and route-mode exploration

`quota.py:budget` counts live A and B spend by local calendar day. The quota sum is
engine-specific. Exploration pauses at dollar/day, quota-impact/day, reported window
stop percentage or a reached-limit flag. Defaults are $5 API-equivalent spend,
20 quota points and 95% window use. If no option fits, A routes deterministically
and B is skipped. This is not a hard circuit breaker for user-directed primary work.

Route mode optionally explores using the existing baseline floor and cost-neighborhood
pool. High stakes and paused budgets force epsilon zero. Otherwise:

<div class="math">q(a|x) = (1-ε)·1[a=a*] + ε·1[a∈pool]/|pool|<br><small>Source: policy.py:choose; default route-mode ε=0</small></div>

The floor is the first tier with shipped probability at least .5 (frontier if none).
The cost neighborhood is at most max(4×greedy cost,.002), falling back to the floor
pool if empty. EV exploration also obeys remaining budgets. Actual use can exceed
estimates, and concurrent processes do not reserve a shared cap atomically.

<!-- page -->
## Shadow comparisons and Bradley-Terry

Shadow is opt-in. A completes first; a different model then receives the same prompt.
Codex uses exec -s read-only with ignored user config, ephemeral history, empty MCP
configuration and hooks disabled. Claude uses plan permissions, empty tools and
strict MCP lists, disabled hooks/settings/skills and no session persistence.
`engines.py:command` assembles these controls.

The installed CLIs resume mutable sessions. Resuming after A would include A's
current answer, rather than an immutable pre-A snapshot. B therefore uses fresh
context, recorded as `context_mode=fresh`. Context-dependent follow-ups should not
receive pair ratings. B never changes A's primary session ID.

### Interaction and pair storage

The terminal prints a collapsed B summary with model, effort, latency and cost.
`/b` at the rating prompt expands its answer in memory; a/b/= means A preferred,
B preferred or tie. `/rate` can subsequently add an absolute A rating. Preference
updates replace the pair's previous target instead of duplicating training data.

<div class="math">q<sub>pair</sub> = q(A|x) · q(B|x,A,post-A budget)<br>y<sub>pair</sub> = 1 for A, &nbsp; 0 for B, &nbsp; 1/2 for tie<br>P(A ≻ B | x) = σ(s<sub>A</sub>(x) - s<sub>B</sub>(x))<br><small>Source: chat.py:chat; storage.py:add_pair, revise; baseline.py:fit</small></div>

s is the shipped action logit plus rich baseline residual. For pair error
δ=y<sub>pair</sub>-P(A≻B), A receives +δψ and B -δψ, with normalized inverse joint
propensity and half the pass-model learning rate. A decisive loss supplies evidence
for verification, but preference alone never becomes a binary pass label.

Each B event records its own conditional propensity, estimated quota impact,
observed tokens, latency and cost. Both events count toward daily caps. High stakes
suppress B unless --shadow-high accompanies --shadow. A failed B launch does not
replace A. Fakes validate subprocess arguments and persistence; no paid sandbox
attack or provider-level enforcement test was run.

Pairwise utility is relative to the presented contexts. It cannot identify absolute
correctness on its own; the shipped prior and absolute ratings anchor pass probability.
Pair preferences also reflect style, latency and other user choices unless the user
consistently applies a correctness-only criterion.

<!-- page -->
## IPS fitting and verification head

`advisor train-baseline` uses the earliest 80% of primary turns by timestamp by
default. B remains with its training A even if it completes after the split. Absolute
training examples must be live randomized collection turns, with positive known
propensity, a rating and a recognized action. Manual choices and backlog are excluded.
Pairs require both participants in the training partition.

<div class="math">v<sub>i</sub> = 1/q<sub>i</sub>, &nbsp; v̄ = (1/n)Σv<sub>i</sub>, &nbsp; α<sub>i</sub> = v<sub>i</sub>/v̄<br>θ<sub>a</sub> ← clip(θ<sub>a</sub> + η<sub>epoch</sub>[α<sub>i</sub>(y<sub>i</sub>-p<sub>i</sub>)ψ(x<sub>i</sub>) - .002θ<sub>a</sub>], -4,4)<br>η<sub>epoch</sub> = .05 / √(epoch+1)<br><small>Source: baseline.py:fit; default 12 epochs</small></div>

Inverse propensity weights are normalized by their sample mean to stabilize SGD.
Ratios are not clipped; coefficients are bounded. Pairs use their own mean inverse
joint propensity and half this learning rate. Persisted action counts sum the
unnormalized inverse propensities of absolute examples. The artifact includes
weights, numeric example counts, cutoff timestamp and opaque training IDs.

The saved baseline is immediately installed and personal residuals replayed against
it. The shipped artifact is unchanged. IPS addresses unequal action selection,
not bias from users choosing which outcomes to rate. Pair-only fitting still relies
on the prior for absolute probability calibration.

### Verification logistic output

The verification head has shared rich coefficients β and action bias b. Target 1
comes from v/w tags, failed tests or decisive pair losses. Otherwise an absolute
rating or passed test supplies target 0; unlabelled rows are omitted.

<div class="math">P(verification | x,a) = σ(b<sub>a</sub> + β·ψ(x))<br>η = .1 / √(1+.02n)<br>β ← clip(β + η[(y-p)ψ - .002β], -4,4)<br>b<sub>a</sub> ← clip(b<sub>a</sub>+η(y-p), -4,4)<br><small>Source: baseline.py:verification_target, fit_verification</small></div>

After 30 eligible examples, chat displays this probability. High stakes still imply
human review and code executable testing. Otherwise p≥.5 recommends cross-model
check, p≥.25 self-check, else none (`baseline.py:verification`). Below 30 examples,
the existing rules apply. This is advice, not a calibrated safety assurance.

<!-- page -->
## Later-turn and off-policy evaluation

`baseline.py:evaluate_later` compares shipped baseline, frozen user baseline and
prequential personal learning on turns after the stored cutoff. Training IDs and
paired training B events are excluded. Personal weights start from the team prior
replayed on training events, then update only after predicting each later turn.
Loading current personal weights here would leak evaluation labels and is avoided.

| Metric | Definition for n absolute-rated observations |
|---|---|
| Log loss | -Σ[y log p+(1-y)log(1-p)]/n; p clipped to [10^-6,1-10^-6] |
| Brier | Σ(p-y)^2/n |
| ECE | Σ bins (n_bin/n) × abs(mean p - mean y); ten equal-width bins |
| Rated-good rate | Σy/n on observed rated actions |
| Spend | Sum of event cost_usd, including estimated values |
| Cost per success | Spend/Σy; null when there are no successes |

**Source:** `baseline.py:metrics`. Empty samples produce null quality metrics,
not zero error. Common observed good rates do not prove the policies would choose
equally good actions. Matched-policy rates and randomized-action estimates are
reported separately under off_policy.

<div class="math">IPS<sub>good</sub> = (1/n)Σ [1(a<sub>i</sub>=π(x<sub>i</sub>))/q<sub>i</sub>] y<sub>i</sub><br>DR<sub>good</sub> = (1/n)Σ {m(x<sub>i</sub>,π(x<sub>i</sub>)) + [1(a<sub>i</sub>=π(x<sub>i</sub>))/q<sub>i</sub>] [y<sub>i</sub>-m(x<sub>i</sub>,a<sub>i</sub>)]}<br>IPS<sub>spend</sub> = (1/n)Σ [1(a<sub>i</sub>=π(x<sub>i</sub>))/q<sub>i</sub>] cost<sub>i</sub><br><small>Source: baseline.py:evaluate_later</small></div>

These use rated randomized primary collection turns with known q and no manual
override and recorded support for the target action. π uses historical decision time
and preceding-turn evidence; m is the corresponding
probability head. This release implements IPS training and doubly-robust evaluation,
not a doubly-robust training optimizer. Unclipped IPS/DR values can leave [0,1] in
small samples; they are estimators, not individual probabilities. High-stakes and
budget eligibility restrict positivity; unsupported rows are omitted. Results apply
to the supported rated subset, not the entire workload.

`priors.py:evaluate` retains the old baseline/team/personal aggregate view under
tiers, with confidence-weighted prequential losses. The chronological comparison
is under heldout. Team priors may overlap local evaluation, ratings may be missing
non-randomly and re-rating changes historical labels. These reports are audit tools,
not causal savings guarantees. Fix a baseline version while collecting later evidence;
retraining moves the cutoff and changes the evaluation population.

<!-- page -->
## Persistent schema and migration

SQLite uses secure_delete and transactional writes. Event JSON is validated and
reconstructed before storage, rather than passing arbitrary engine objects through.
Settings contain internal numeric artifacts; config has its own allowlist. The
default directory is ~/.task-advisor, overridable with TASK_ADVISOR_HOME.

| Table / artifact | Columns or fixed fields |
|---|---|
| events | id TEXT PRIMARY KEY; row TEXT NOT NULL, validated JSON |
| settings | key TEXT PRIMARY KEY; value TEXT NOT NULL |
| manifest | identity TEXT PRIMARY KEY; value TEXT NOT NULL |
| pairs | a TEXT PRIMARY KEY; b TEXT UNIQUE NOT NULL; preference REAL nullable; propensity REAL NOT NULL |
| Event envelope | id, schema, time, source, engine, model, effort, features, indicators, propensity, predicted, rating, tags, label |
| Personal/team settings | Catalog action → 13 bounded numbers; effective counts |
| Verification setting | Shared coefficients, action biases, labelled-example count |
| User baseline setting/file | version, cutoff, training_ids, weights, counts, rated_examples, pair_examples |
| Manifest value | Whole-file hash, mtime, since filter, opaque event IDs |
| Team export | version, deltas, counts only |
| Evaluation export | evaluation, tiers, observed_total_spend_usd, heldout |

Envelope enums and numerical constraints are documented completely in PRIVACY.md.
IDs are UUID hex, ratings null/0/1, tags bounded letters and probabilities [0,1].
Pair preference is null/0/.5/1 and its joint propensity is positive.

### Upgrade behavior

`storage.py:connect` creates pairs and checks PRAGMA user_version. Below version 3,
it adds nullable v3 features to events and sets their schema to 3, then updates
user_version transactionally. Existing ratings, labels and 13-coordinate weights
remain valid. Dropped text cannot be recovered, so old counts/context/clusters stay
unknown. Backlog retains file-level idempotence and existing ratings without
inventing randomized propensity. Packaged ingest reader dependencies are unchanged.

### Retention and deletion

`advisor forget` removes the database/journals and recognized local update, eval and
user-baseline artifacts. It retains config and unrelated files and does not follow
symlinks outside the state directory. Stop active processes first. Exports elsewhere,
engine histories and backups need separate deletion. Logical deletion is not a
forensic SSD/backups guarantee. No automatic expiry or database encryption is provided.
Old v0.1 text-bearing state is never imported and should be removed on upgrade.

<!-- page -->
## Team aggregation and training roadmap

Team sharing is manual. Export contains only the 13-coordinate personal/team
residuals and effective local action counts. The richer baseline, verification head,
examples, timestamps and task features are not included.

<div class="math">d<sub>u,a</sub> = clip(w<sub>u,a</sub>/max(1,‖w<sub>u,a</sub>‖<sub>2</sub>) + ε, -4,4)<br>ε<sub>j</sub> ∼ N(0,σ<sup>2</sup>)<br>d̄<sub>a,j</sub> = Σ<sub>u</sub> n<sub>u,a</sub>d<sub>u,a,j</sub> / Σ<sub>u</sub> n<sub>u,a</sub><br><small>Source: priors.py:export_update, merge_updates; zero count gives zero vector</small></div>

--noise sets per-coordinate Gaussian standard deviation after L2 clipping. Counts
are not noised: this is data minimization, not formal differential privacy or
anonymity. Workload characteristics can still leak. Duplicate update files receive
duplicate weight, so use one current export per participant. `use-prior` validates
schema/action dimensions, installs the team vector and replays local events. No
upload or network request occurs.

A local baseline changes the reference logit corrected by personal residuals.
Merging residuals trained against different baselines is therefore heuristic.
Meaningful team use should agree on a common baseline/version and disclose whether
participants contributed to evaluation. Automatic rich-baseline federation is not
implemented in this version.

### Evidence-led progression

| Stage | Operation and promotion criterion |
|---|---|
| Collect | Randomize, record q and rate turns; inspect action/category coverage, not just count |
| User baseline | Fit IPS absolute labels and BT preferences; reserve later turns; inspect calibration and sparse actions |
| Personal | Replay residuals and update prequentially; enable verification head after 30 eligible examples |
| Team | Manually merge clipped numeric updates with a common reference baseline; evaluate newly collected later turns |
| Company | Proposed version governance, cohort holdouts, retention and controlled rollout; not shipped |

N=300 is an operational starting target, not statistical certification. Across 21
model/effort actions in two engines, 300 turns can leave categories sparse. Caps,
high-stakes guards and manual overrides reduce coverage further. Continue collection
when the dashboard reveals gaps, and avoid interpreting small prediction differences
as measured capability. Success requires useful ratings as well as execution volume.

<!-- page -->
## Validation and operating limits

Distribution tests cover normalization, inverse coverage, high-stakes eligibility,
manual choices and cap fallback. Fake-engine subprocess tests inspect Codex/Claude
flags, resumed A sessions, isolated B runs, pair propensities, ratings and collapsed
display. Synthetic rollout fixtures test quota windows, credits and reasoning/cache
tokens. Toy data tests verify IPS influence and Bradley-Terry score movement.

Migration/privacy tests retain ratings while adding unknown v3 fields, reject
free-text metadata and scan state for canaries. Holdout tests keep reserved later
turns out of baseline fitting. Verification tests exercise the 30-example gate.
Threshold is the default; explicit EV tests cover its optional objective. Existing advisor, ingest, model and task tests pass together.

### Reproducible workflow

```powershell
python -m pip install dist/task_advisor-0.3.2-py3-none-any.whl
advisor backlog --codex --claude
advisor chat --engine codex
advisor serve
advisor train-baseline
advisor eval
```

```powershell
python -m pytest advisor/tests -q
python advisor_build.py
python -m pip wheel . -w dist --no-deps
python docs/build_advisor_pdf.py
```

The wheel and source ZIP include synthetic artifacts. The ZIP includes tests and
this document. The PDF uses installed headless Chrome/Edge, adapting
business/build_pdf.py with offline HTML mathematics. Raster inspection checks
tables, formula layout, typography and diagram labels.

### Material limits

No paid provider call, real account-model admission or destructive-tool sandbox
attack was run. Installed CLI help and fakes check command construction and parsing,
not provider enforcement. Claude remaining quota was absent from the histories
inspected. Quota deltas can include other clients; predictions can exceed caps;
multiple processes do not reserve budgets atomically.

Fresh B context can invalidate follow-up comparisons. Preference cannot establish
absolute correctness. ASCII language guessing, regex counts and capped workspace
traversal are approximate. Sparse coverage, selective ratings, synthetic transfer
and team/evaluation overlap limit statistical claims. Full replay can become slow
on large backlogs. MCP/one-shot advice preserves the legacy tier interface;
collection, EV and learned verification advice are chat features.
