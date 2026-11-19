# Task advisor

Task advisor picks the cheapest AI model and effort likely to succeed for a task, says whether to cross-verify the answer, and learns locally from your one-tap ratings. This is a prototype: pass probabilities and model mappings are estimates, with limited real-world validation.

## Try it in your browser

[Open the demo](https://alexvillagomez.github.io/task-advisor/demo/), or open demo/index.html via any static server:

```sh
python -m http.server -d demo
```

Visit http://localhost:8000. Tasks stay in browser memory. The demo uses the shipped baseline and default routing settings; it does not learn from ratings.

## Install

Requires Python 3.10+. For live chat, install and authenticate the Codex or Claude CLI separately.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install git+https://github.com/alexvillagomez/task-advisor
```

macOS / Linux:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install git+https://github.com/alexvillagomez/task-advisor
```

Alternatively, download a release wheel and install it in the activated environment:

```sh
python -m pip install ./task_advisor-0.3.2-py3-none-any.whl
```

The runtime has no third-party Python dependencies. From a source checkout, `python -m pip install .` also works.

## Use it

```sh
advisor "Write a Python function to sort records and add tests"
advisor serve
```

Open the local dashboard at http://127.0.0.1:8765. The direct command and dashboard give tier advice; live chat routes to an engine-specific model and effort.

Import history before using the backlog report. Omit sources you do not use; replace the ZIP path with your ChatGPT export.

```sh
advisor backlog --codex --claude --chatgpt-export PATH.zip --dry-run
advisor backlog --codex --claude --chatgpt-export PATH.zip
advisor backlog --report
```

Imports read local transcripts in memory and retain only allowed features and outcomes. Repeated imports avoid duplicate weight. Historical success labels are weak; review coverage and unknowns before trusting personalization. ZIP files are read without extraction. Compressed `.zst` files need system libzstd or optional `zstandard`. See the [backlog explainer](advisor/BACKLOG_EXPLAINER.md) for background; its proposed v0.2 design notes are historical.

```sh
advisor chat --engine codex --mode route
advisor chat --engine codex --mode collect
```

`route` selects the cheapest action meeting the target. `collect` samples actions to gather ratings, subject to collection caps. Live engine calls can incur provider charges. Enter skips a rating; `/rate` changes a previous rating. `/model` selects a model, `/model auto` resumes routing, `/stakes high` changes stakes, and `/quit` exits.

| Rating | Meaning |
|---|---|
| `+` / `-` | Good / bad answer |
| `v` | Verification needed |
| `o` / `u` | Overkill / underpowered |
| `w` / `p` | Wrong / partial |
| `s` / `c` | Slow / costly |
| `-vw`, `+o` | Combine a rating and tags |
| `/rate -c` | Add or change an absolute rating |

Claude Code hooks:

```sh
advisor claude-code install --statusline
advisor claude-code uninstall
```

Restart Claude Code after installation. Hooks recommend a model and capture ratings; they cannot switch models. Use Claude Code’s `/model sonnet` (or haiku/opus) and effort controls yourself. Submit a standalone `+`, `-`, or `/rate -c` after an answer to rate it without a provider request. The optional status line supplies the current model and effort; existing status lines are preserved. Installation backs up changed settings in `~/.claude/`.

## How it works

- Extracts fixed text features locally, without saving the text.
- Uses a small bag-of-words predictor trained on synthetic graded trials.
- Adjusts pass estimates for code, external facts, ambiguity and task length.
- Maps the three capability tiers to the bundled engine/model catalog.
- Uses stakes targets of 72%, 88% and 95%, with an effort adjustment and default latency penalty.
- Recommends a setup and verification method, and flags weak evidence.
- Learns personal numerical adjustments from ratings; backlog labels have reduced weight.

See the [technical document](docs/ADVISOR_TECHNICAL.pdf) ([Markdown](docs/ADVISOR_TECHNICAL.md)). Catalog prices and capabilities are snapshots; edit the local catalog as needed.

## Privacy

- No prompt or response text is stored in advisor-owned state; only allowed numerical features, enums and outcomes.
- State stays in local SQLite at `~/.task-advisor/advisor.sqlite3`; `TASK_ADVISOR_HOME` overrides the directory.
- No telemetry or automatic uploader. Explicit exports contain numerical updates or aggregate metrics.
- Live chat sends prompts to the selected provider through its CLI. Provider histories are separate.
- See the complete [privacy boundary](advisor/PRIVACY.md), including retention and deletion limits.

## Known limitations

- The first Claude Code prompt can show the current model as `unknown` until it is observed.
- The Claude engine is lightly tested; fake-engine tests do not establish provider compatibility.
- The baseline was trained on synthetic graded trials. Transfer to current model families is an uncalibrated prior.
- Backlog labels are weak, historical model/effort can be unknown, and summaries may be difficult to recognize without text.
- Browser advice omits personal learning, quotas and SQLite. Estimated probabilities are not guarantees.

## Running tests

Python tests use pytest and fake engines; Node 18+ runs browser parity with no npm dependencies. Install pytest separately if needed.

```sh
python -m pytest advisor/tests -q
python -B demo/make_fixtures.py
node demo/parity.test.mjs
```

Parity covers 40 prompts × 3 stakes × 2 engines, including every fast text feature and tier probability. To build a wheel without a build frontend:

```sh
python -c "from pathlib import Path; import advisor_build; Path('dist').mkdir(exist_ok=True); print(advisor_build.build_wheel('dist'))"
```

## Use it live in ChatGPT and Claude.ai

1. Start `advisor serve` and keep it running (default port 8765).
2. Open `chrome://extensions` (or `edge://extensions`), enable **Developer mode**, choose **Load unpacked**, and pick this release's `extension/` directory.
3. Open or reload chatgpt.com or claude.ai. While typing, the chip above the composer shows the recommended model/effort, predicted success, verification need and complexity. Click to expand tier probabilities, reasons and setup advice. When detectable, it also shows your selected model.
4. After a reply finishes, optionally toggle tags: **v** verification needed, **o** overkill, **u** underpowered, **w** wrong, **p** partial, **s** slow, **c** costly. Click 👍 or 👎 once to submit. The recommendation freezes when you send.

Prompt and reply text stay in the page; prediction runs locally. Only fixed numerical/enum features, a catalog recommendation, catalog-normalized site model and rating/tags go to the local server. The existing storage schema records the recommendation as model/effort/predicted and the observed site model as `features.previous_model`; source is `live`. The extension marks its payload with `client: extension`. The server discards extra fields. Ratings rebuild the local policy; extension predictions use the bundled baseline, not personal weights.

If the server is unavailable, the chip says **not saving — run `advisor serve`**. Up to 500 feedback events queue locally (oldest dropped at capacity), retrying every 15 seconds while a supported tab is open and when the browser starts. Delivery is best effort; a lost server acknowledgement can cause a duplicate. No remote code, telemetry or provider API calls are used.

Site layout changes can break composer, send/stop, response and model selectors (centralized in `extension/content.js`). Reply completion is best effort with a 120-second fallback; cancelled sends can also produce a rating row. Unrated completed turns appear in order and remain in page memory until navigation or reload. Native desktop apps are not supported. The extension recommends models but does not switch them.

After updating demo predictor/data or the payload module, run `node extension/sync.mjs` from this directory, then reload the extension. No npm install is required. Verify feedback privacy with `node extension/payload.test.mjs`.

No license has been selected by the owner yet.

## License

No open-source license yet: all rights reserved. You are welcome to read the code, try the demo and install it for personal evaluation. Please ask before reusing or redistributing the code.
