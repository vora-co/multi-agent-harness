# Multi-Agent Harness

A multi-agent harness that automatically builds web applications feature by feature — using five specialized AI agents: **Leader**, **Spec Writer**, **Implementer**, **Reviewer**, and **E2E Tester**.

You define what to build in `feature_list.json`. The harness does the rest.

---

## Compatible LLM providers

The harness uses the **OpenAI-compatible SDK**, which means it works with any LLM provider that exposes an OpenAI-compatible API. DeepSeek is the default because it offers excellent cost/performance for agentic workloads, but switching to another provider is a two-line change in `harness.py`:

```python
# Default — DeepSeek
MODEL    = "deepseek-v4-pro"
client   = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com")

# OpenAI
MODEL    = "gpt-4o"
client   = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Anthropic (via compatible proxy)
MODEL    = "claude-sonnet-4-5"
client   = OpenAI(api_key=os.getenv("ANTHROPIC_API_KEY"), base_url="https://api.anthropic.com/v1")

# Any other OpenAI-compatible provider
MODEL    = "your-model-name"
client   = OpenAI(api_key=os.getenv("YOUR_API_KEY"), base_url="https://your-provider.com/v1")
```

Update your `.env` accordingly with the right key for your provider.

---

## How it works

```
You → define features in feature_list.json
         ↓
    👑 Leader        — reads your feature list, coordinates the pipeline
         ↓
    📋 Spec Writer   — writes a detailed technical spec before any code is written
         ↓
    🔨 Implementer   — writes the code and tests following the spec
         ↓
    🧪 E2E Tester    — runs Playwright browser tests (only if e2e: true)
         ↓
    🔍 Reviewer      — validates tests pass and approves or rejects
         ↓
    ✅ Feature marked done — Leader moves to the next one
```

If the Reviewer rejects, the Implementer retries with the rejection reason injected. The harness retries up to `MAX_RETRIES_REVIEW` times before marking a feature as `failed`.

---

## Prerequisites

- Python 3.9+
- Node.js 18+ (only if building frontend features)
- An API key from your chosen LLM provider ([DeepSeek](https://platform.deepseek.com/), [OpenAI](https://platform.openai.com/), or any OpenAI-compatible provider)

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/your-username/multi-agent-harness
cd multi-agent-harness
```

### 2. Configure your API key

Create a `.env` file in the root with the key for your chosen provider:

```env
# DeepSeek (default)
DEEPSEEK_API_KEY=your_api_key_here

# Or OpenAI
OPENAI_API_KEY=your_api_key_here
```

Then update `MODEL` and `client` in `harness.py` to match. See the [Compatible LLM providers](#compatible-llm-providers) section above.

### 3. Install dependencies

```bash
bash init.sh
```

This installs all Python dependencies from `requirements.txt` and verifies the project structure.

---

## Define your features

Edit `feature_list.json` to describe what you want to build. Each entry is a feature:

```json
[
  {
    "id": 1,
    "title": "User domain model",
    "description": "Create src/models/user.py with a User class (id: int, name: str, email: str, role: str). Validate email with regex. Write tests in tests/test_user.py: valid creation, invalid email raises ValueError, to_dict/from_dict round-trip.",
    "status": "pending",
    "e2e": false,
    "depends_on": [],
    "created_at": "2025-01-01T00:00:00"
  },
  {
    "id": 2,
    "title": "REST API: user authentication",
    "description": "Create src/auth.py with JWT auth (python-jose). Add POST /api/v1/auth/register and POST /api/v1/auth/login endpoints to src/api.py. Hash passwords with bcrypt. Return JWT token with payload {user_id, role, exp: 24h}. Tests in tests/test_auth.py.",
    "status": "pending",
    "e2e": false,
    "depends_on": [1],
    "created_at": "2025-01-01T00:00:00"
  }
]
```

### Feature fields

| Field | Type | Description |
|---|---|---|
| `id` | int | Sequential ID |
| `title` | string | Short name for the feature |
| `description` | string | Full spec: files to create, logic to implement, tests to write |
| `status` | string | `pending` \| `in_progress` \| `done` \| `failed` |
| `e2e` | bool | `true` only for features with browser UI to test with Playwright. Keep `false` for backend features |
| `depends_on` | int[] | IDs of features that must be `done` before this one runs. Use `[]` for no dependencies |
| `created_at` | string | ISO timestamp |

### Feature dependencies

The `depends_on` field lets you declare that a feature requires one or more others to be completed first. The harness resolves the full dependency graph on startup and injects a computed execution order into the Leader's context — the Leader does not need to infer ordering itself.

```json
{ "id": 3, "title": "Protected API endpoints", "depends_on": [1, 2] }
```

The harness resolves this into:

```
#1 (User domain model) → #2 (REST API: user auth) → #3 (Protected API endpoints)
```

At startup, the resolved order is printed to the terminal:

```
Execution order (depends_on resolved): #1 → #2 → #3
```

**Rules:**
- A feature with `"depends_on": []` (or omitting the field entirely) has no prerequisites and can run first.
- `depends_on` accepts a list of feature IDs. All listed IDs must be present in `feature_list.json`.
- A feature cannot depend on itself.
- Circular dependencies are not allowed (e.g. #2 depends on #3 and #3 depends on #2).

**Error handling:** The harness validates the graph on startup using `_validate_dependencies()` in `harness.py`. If errors are found, they are printed as a panel in the terminal and logged to `progress/harness.log`. The harness will still start, but the Leader will receive the error list in its context and will refuse to process features until the graph is fixed. Fix the issue in `feature_list.json` and restart.

### Tips for writing good feature descriptions

- **Be specific about file paths** — "Create `src/models/user.py`" is better than "create a user model"
- **List the tests explicitly** — tell the implementer exactly what scenarios to cover
- **Reference existing files** — "Add endpoints to the existing `src/api.py`" prevents duplication
- **One deliverable per feature** — keep each feature independently testable
- **Use `e2e: false` for backend features** — saves tokens and avoids Playwright setup issues

---

## Run the harness

```bash
python3 harness.py
```

This opens an interactive REPL. Type your instruction and press Enter:

```
You → process all pending features
```

### REPL commands

| Command | What it does |
|---|---|
| `process all pending features` | Processes all pending features in order |
| `run only feature 3 and stop` | Processes only feature #3 |
| `process features 2 and 3` | Processes a specific range |
| `/features` | Shows the status of all features |
| `/costs` | Shows token usage and estimated cost for this session |
| `/budget` | Shows current spend vs. budget limit with a progress bar |
| `/status` | Shows the current state (progress/current.md) |
| `/quit` | Exits the harness |

---

## Project structure

```
multi-agent-harness/
├── harness.py              # Main engine — REPL + leader loop
├── tools.py                # Tools available to agents (file I/O, bash, etc.)
├── feature_list.json       # Your feature definitions ← edit this
├── requirements.txt        # Python dependencies
├── init.sh                 # Setup script
├── .env                    # API key (not committed)
├── agents/
│   ├── leader.py           # Coordinates the pipeline
│   ├── spec_writer.py      # Generates technical specs before implementation
│   ├── implementer.py      # Writes code and tests following the spec
│   ├── reviewer.py         # Validates implementation and approves/rejects
│   └── e2e_tester.py       # Runs Playwright tests (only if e2e: true)
├── progress/               # Agent reports per feature (auto-generated)
│   ├── spec_N.md           # Spec written by Spec Writer
│   ├── impl_N.md           # Implementation report with test results
│   └── review_N.md         # Reviewer verdict
└── data/                   # Runtime data (auto-generated, not committed)
```

---

## Resuming after a crash

The harness has automatic checkpointing. Features stuck in `in_progress` are reset to `pending` on startup. If a feature is marked `failed` and you want to retry it:

```bash
python3 -c "
import json
with open('feature_list.json') as f: features = json.load(f)
for feat in features:
    if feat['id'] == 3:
        feat['status'] = 'pending'
with open('feature_list.json', 'w') as f: json.dump(features, f, indent=2)
"
```

Then restart the harness and run that feature again.

---

## Configuration

Key settings in `harness.py`:

| Variable | Default | Description |
|---|---|---|
| `MODEL` | `deepseek-v4-pro` | Fallback model for any role not listed in `MODEL_BY_ROLE` |
| `MODEL_BY_ROLE` | see below | Per-agent model overrides — edit to tune cost vs. quality |
| `ORCHESTRATOR` | `local` | Execution mode: `local` (plain Python) or `prefect` (dashboard + scheduling) |
| `COST_BUDGET_USD` | `0` | Max USD spend per session. `0` disables enforcement. Example: `COST_BUDGET_USD=2.00` |
| `MAX_ITER_LEADER` | `30` | Max iterations for the Leader agent |
| `MAX_ITER_IMPL` | `50` | Max iterations for the Implementer |
| `MAX_ITER_REVIEWER` | `40` | Max iterations for the Reviewer |
| `MAX_RETRIES_REVIEW` | `2` | Times the impl→review cycle retries before marking failed |

### Per-agent model selection

Each agent uses the model assigned to its role in `MODEL_BY_ROLE`. The defaults balance cost and quality:

| Role | Default model | Rationale |
|---|---|---|
| `leader` | `deepseek-v4-pro` | Multi-step orchestration requires strong reasoning |
| `spec_writer` | `deepseek-v4-flash` | Structured output from a clear template — cheaper model is fine |
| `implementer` | `deepseek-v4-pro` | Code generation benefits from the highest-quality model |
| `reviewer` | `deepseek-v4-flash` | Reads files and runs tests — no deep reasoning needed |
| `e2e_tester` | `deepseek-v4-flash` | Executes existing test scripts mechanically |
| `compaction` | `deepseek-v4-flash` | Context summarization — fast and cheap |

To change a role's model, edit the value in `MODEL_BY_ROLE` in `harness.py`. No other code needs to change. To add a new custom role, insert a new key — `run_agent` picks it up automatically.

> **Cost note:** The pricing constants `_PRICE_INPUT` / `_PRICE_OUTPUT` in `harness.py` are set for DeepSeek v4-pro. If you mix models with different per-token prices, update those constants or the USD estimate will be approximate. Token counts per role in `progress/session_costs.json` are always exact regardless.

---

## Prefect integration

The harness supports an optional Prefect mode that adds dashboard observability, scheduling, and a foundation for future parallel execution — without changing any agent logic or tool behavior.

### What changes in Prefect mode

| | Local mode (`ORCHESTRATOR=local`) | Prefect mode (`ORCHESTRATOR=prefect`) |
|---|---|---|
| Each REPL command | Plain Python call | Named Prefect **flow run** |
| Each feature cycle | Plain Python function | Prefect **task** tracked inside the flow |
| Dashboard | None (terminal only) | Prefect UI with state, logs, duration per feature |
| Scheduling | Manual REPL input | Cron or interval via `prefect deployment` |
| Dependencies | None | `prefect>=3.0` |

The `@flow` and `@task` decorators are **no-ops** in local mode — they resolve to identity functions at import time, so there is zero runtime overhead and no behavioral difference.

### Setup — local Prefect server (no cloud account)

```bash
pip install -r requirements-prefect.txt
prefect server start          # starts the UI at http://127.0.0.1:4200
```

Then add to your `.env`:

```env
ORCHESTRATOR=prefect
```

Run the harness normally — flow runs will appear in the local UI.

### Setup — Prefect Cloud (free Hobby plan)

```bash
pip install -r requirements-prefect.txt
prefect cloud login           # authenticate once via browser
```

Add to your `.env`:

```env
ORCHESTRATOR=prefect
```

Each harness session streams to your Prefect Cloud workspace at [app.prefect.io](https://app.prefect.io). The free Hobby tier includes workflow observability, logging, and alerting with 7-day run retention — sufficient for development and demos.

### How it works internally

`run_feature_cycle` is decorated with `@task(name="feature-cycle")`. Each call to process a feature appears as a child task inside the flow run. `run_leader` itself is **not** decorated — it contains the LLM agent loop and remains plain Python. The entry point `_run_leader_flow` (a thin `@flow` wrapper) is what `main()` calls; in local mode it is identical to calling `run_leader` directly.

To switch back to local mode, remove `ORCHESTRATOR=prefect` from your `.env` or set it to `local`. No other changes needed.

---

## Safe write directories

Agents can only write to these directories (controlled in `tools.py`):

```python
SAFE_WRITE_DIRS = ("src/", "tests/", "progress/", "docs/", "frontend/", "data/")
```

Add more directories here if your project needs them.

---

## Plugin system

The harness exposes a lifecycle hook system that lets you extend behavior without modifying any base file. This is the foundation for an open-core fork: the public repo ships with an empty `plugins/` directory; a premium fork adds modules there.

### How it works

At startup, the harness calls `_load_plugins()`, which imports every `*.py` file in `plugins/` alphabetically. Each module registers callbacks via `register_hook()` at import time. The harness fires the registered callbacks at key points in the pipeline.

Files whose names start with `_` are skipped by the loader — use `_disabled_plugin.py` to park code that isn't ready.

### Available events

| Event | When it fires | Keyword arguments |
|---|---|---|
| `before_feature` | Start of each feature cycle, before spec or code | `feature_id`, `description`, `e2e` |
| `after_spec_generated` | After spec is written and validated | `feature_id`, `spec_path`, `issues` (list) |
| `after_feature_approved` | Reviewer approves the feature | `feature_id`, `description`, `attempts` |
| `after_feature_failed` | Feature exhausts all retries | `feature_id`, `description`, `attempts`, `final_verdict` |
| `after_session` | Harness exits (including on crash) | `session_costs` (dict) |

### Writing a plugin

Create a `.py` file in `plugins/`. Call `register_hook()` at module level:

```python
# plugins/my_plugin.py
from harness import register_hook

def on_approved(feature_id: int, description: str, attempts: int, **kwargs):
    print(f"Feature #{feature_id} done in {attempts} attempt(s)")

register_hook("after_feature_approved", on_approved)
```

Always add `**kwargs` to callback signatures — new arguments may be added to events in future versions and `**kwargs` keeps your plugin compatible.

See `plugins/example_plugin.py` for a fully documented template with commented-out examples for Slack notifications, GitHub PR creation, external logging, and session cost reporting.

### Rules

- Plugins import from `harness`, never the other way around.
- Callbacks run synchronously in the main thread — keep them fast. For slow operations (HTTP, file uploads), spawn a background thread.
- Errors inside callbacks are caught and logged; they never stop the pipeline.

### Open-core fork setup

To maintain a premium fork that extends the public harness:

```bash
# Initial setup (one time)
git clone <public-repo-url> multi-agent-harness-premium
cd multi-agent-harness-premium
git remote rename origin upstream
git remote add origin <private-repo-url>
git remote set-url --push upstream DISABLE   # prevent accidental push to public
git push -u origin main
```

Add premium-only modules to `plugins/`. Never edit `harness.py` or any other base file in the premium fork — all extensions go through the plugin system.

To pull upstream improvements from the public repo into the premium fork:

```bash
git fetch upstream
git merge upstream/main    # conflicts are rare because premium only adds files
git push origin main
```

---

## How agents communicate

Agents don't pass results through chat — they write to files in `progress/`. This prevents context bloat and keeps each agent focused on its task.

**Caching:** If a spec or impl file already exists and shows passing tests, the harness reuses it instead of regenerating — saving tokens on retries.

---

## Costs

Token usage is tracked per agent. Run `/costs` in the REPL or check `progress/session_costs.json` after a session.

Typical cost per feature: **~$0.05–0.15 USD** with DeepSeek v4-pro depending on complexity.

---

## Roadmap

Known improvements identified during development. Contributions welcome.

### Storage backends
Currently the harness writes everything to JSON files in `data/` and `progress/`. This works well for small projects but doesn't scale. Planned improvements:
- **Pluggable storage layer** — abstract `tools.py` reads/writes behind an interface so you can swap JSON files for SQLite, PostgreSQL, MongoDB, or any other backend without changing agent logic
- **Remote storage** — support S3 or Google Cloud Storage for `progress/` reports, enabling distributed or cloud-based runs

### Agent improvements
- **Parallel feature execution** — independent features could run simultaneously instead of sequentially, reducing total build time significantly
- ~~**Feature dependency graph**~~ ✅ — `depends_on` field added to `feature_list.json`; harness validates and resolves the graph on startup and injects execution order into the Leader's context
- ~~**Per-agent model selection**~~ ✅ — `MODEL_BY_ROLE` dict in `harness.py` assigns a model per role; heavier roles (`leader`, `implementer`) use `pro`, mechanical roles use `flash`
- **Agent memory across features** — agents currently start fresh each feature; persisting learned conventions and decisions across features would reduce repeated mistakes

### Parallel execution
The current pipeline is strictly sequential: one feature at a time, one agent at a time. In real engineering teams this never happens. Planned improvements:
- **Concurrent feature workers** — spawn an agent pool where features without dependencies run simultaneously using `asyncio` or `concurrent.futures`; implement a shared lock manager to prevent agents from writing to the same file
- **DAG-based scheduler** — replace the ordered list in `feature_list.json` with a directed acyclic graph; the harness computes which features are unblocked at any point and dispatches them in parallel
- **Shared context bus** — agents working in parallel need to broadcast decisions (e.g. "I created `src/models/user.py`") so sibling agents don't duplicate work; implement a lightweight pub/sub layer over the `progress/` directory
- **Token budget manager for parallel runs** — parallel execution multiplies cost; add a concurrency cap (e.g. max 3 features at once) and a global session token budget that pauses new dispatches when the limit is approached
- **Human-in-the-loop gates** — in hybrid human+agent teams, some checkpoints require human approval before the next parallel batch runs; add an optional `requires_human_gate: true` field on features that pauses the DAG until a human signs off

### SDLC governance and DevOps integration
The harness currently operates outside the software delivery lifecycle — it produces code but doesn't integrate with the version control and quality pipeline that real teams depend on. Planned improvements:
- **Automatic branch and PR creation** — each feature runs on its own Git branch (`feature/N-title`); on Reviewer approval the harness opens a pull request automatically via GitHub/GitLab API with the spec, implementation report, and test results as PR description
- **SAST integration** — run static analysis (Bandit for Python, ESLint security plugin for JS/TS, Semgrep) as a mandatory gate inside the Reviewer step; block approval if high-severity findings are present
- **DAST integration** — for features with `e2e: true`, spin up the app in a sandbox and run OWASP ZAP or Nuclei against it; attach the report to the PR before merge
- **SonarQube / SonarCloud gate** — push coverage and code-quality metrics to Sonar after each feature; the Reviewer reads the quality gate result and rejects if coverage drops below threshold or new code smells are introduced
- **Full traceability chain** — link every artifact: `feature_list.json` entry → `spec_N.md` → `impl_N.md` → Git commit SHA → PR number → deployment tag; store this chain in `progress/trace_N.json` so auditors can follow a feature from business requirement to production
- **Dependency vulnerability scanning** — after the Implementer adds a new package, run `pip-audit` or `npm audit` and fail the feature if critical CVEs are introduced
- **Compliance artifact generation** — auto-generate SBOM (Software Bill of Materials) and security summary reports per release, useful for enterprise clients with compliance requirements

### Harness UX
- ~~**Cost budgets**~~ ✅ — set `COST_BUDGET_USD=N` in `.env`; harness finishes the current agent step then stops; `/budget` REPL command shows a live progress bar
- ~~**Web dashboard**~~ ✅ (via Prefect) — set `ORCHESTRATOR=prefect` to get a live UI with agent progress, feature status, logs, and duration; see [Prefect integration](#prefect-integration)
- **Cost budgets** — set a maximum spend per session or per feature; the harness stops and alerts when the budget is reached
- **Webhook notifications** — notify Slack, email, or any webhook when a feature completes or fails; Prefect mode already supports this natively via [Automations](https://docs.prefect.io/v3/automate/events/automations-overview)

### Reliability
- ~~**Smarter retry logic**~~ ✅ — `_extract_retry_context()` parses pytest output to inject only failing test names and key error lines on retries; reduces per-retry token cost 40–70% vs. injecting the full rejection
- ~~**Spec validation**~~ ✅ — after generating a spec, `_validate_spec()` cross-checks it against the existing file tree with a cheap LLM call; any contradictions or false assumptions are appended as a warning section in the spec file before the Implementer reads it
- **Incremental context compaction** — the current compaction strategy is conservative; a more aggressive approach could reduce token usage on long sessions by 30–40%


---

## Changelog

### v1.5.0
- **Plugin system** — lifecycle hook registry (`before_feature`, `after_spec_generated`, `after_feature_approved`, `after_feature_failed`, `after_session`) with `register_hook()` / `_fire()` API; `_load_plugins()` auto-imports all `*.py` files in `plugins/` at startup; `plugins/example_plugin.py` ships as a fully documented template; designed for open-core forks that extend the harness without touching base files

### v1.4.0
- **Cost budgets** — `COST_BUDGET_USD` env var sets a per-session spend limit; `_track_usage` sets `_BUDGET_EXCEEDED` flag when the limit is hit; `run_feature_cycle` skips new work gracefully; `/budget` REPL command shows a live spend-vs-limit progress bar
- **Smarter retry logic** — `_extract_retry_context()` parses rejection reasons for pytest `FAILED` lines and unique exception messages; injects only the actionable subset into the next implementer attempt instead of the full rejection text
- **Spec validation** — `_validate_spec()` makes a single cheap LLM call after each new spec is generated, cross-checking against the existing file tree; contradictions and false assumptions are appended as a `## ⚠ Spec validation warnings` section in the spec file before the Implementer reads it; fully non-blocking (failures are silently skipped)

### v1.3.0
- **Prefect integration** — optional `ORCHESTRATOR=prefect` mode wraps execution in `@flow`/`@task` for dashboard observability, scheduling, and a foundation for parallel execution; local mode is unchanged (decorators are no-ops). Install with `pip install -r requirements-prefect.txt`

### v1.2.0
- **Per-agent model selection** — `MODEL_BY_ROLE` dict in `harness.py` assigns a model per role; `run_agent`, `_compact_messages`, and the leader loop all respect it. Reduces session cost ~30–40% with default assignments (`flash` for spec, reviewer, e2e, compaction; `pro` for leader and implementer)
- **Feature dependency graph** — `depends_on: []` field added to `feature_list.json`; `_topological_sort` and `_validate_dependencies` in `harness.py` resolve execution order and detect cycles/missing IDs on startup; resolved order is injected into the Leader's context so it never has to infer ordering itself

### v1.1.0
- All code, comments, and agent prompts translated to English
- REPL commands updated to English (`/quit`, `/costs`, `/status`)
- Added Roadmap section to README

### v1.0.0
- Initial release — multi-agent harness with Leader, Spec Writer, Implementer, E2E Tester, Reviewer
- OpenAI-compatible SDK — works with DeepSeek, OpenAI, Anthropic, and any compatible provider
- Spec and impl caching — reuses existing reports on retries to save tokens
- Pre-injected file tree context — eliminates exploratory reads at the start of each agent cycle
- Lightweight frontend reviewer mode — skips Playwright when `e2e: false`
- Automatic checkpointing — recovers `in_progress` features after crashes

---

## License

MIT
