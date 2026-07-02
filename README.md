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

`id`, `title`, `description`, and `status` are required. `e2e`, `depends_on`, and `created_at` are optional — omitting them defaults to `false`, `[]`, and unset respectively.

### Schema validation

`feature_list.json` entries are validated against a versioned pydantic schema, `FeatureSchema` in `harness.py` (current version: `FEATURE_SCHEMA_VERSION = "1.0"`). The schema rejects unknown fields (`extra="forbid"`) — this is what catches a typo like `"depnds_on"` instead of `"depends_on"`, which would otherwise be silently ignored by every `dict.get(...)` call in the codebase and just sit there as dead JSON with no error and no dependency enforcement.

Beyond the table above, these fields are also accepted because something in the harness (or a premium plugin) writes them:

| Field | Written by | Purpose |
|---|---|---|
| `updated_at` | `update_feature_status()` (`tools.py`), `recover_stale_features()` (`harness.py`) | Last-modified timestamp |
| `recovery_note` | `recover_stale_features()` (`harness.py`) | Explains why a feature was reset to `pending` after a crash |
| `_checkpoint` | `_save_checkpoint()` (`harness.py`) | Resume point (`step`, `attempt`, `saved_at`) for crash recovery — see [Resuming after a crash](#resuming-after-a-crash) |
| `requires_human_gate` | Premium **Human-in-the-loop gates** plugin | Pauses the pipeline before the Spec Writer runs until a human approves — see [⭐ Premium modules](#-premium-modules) |

You never need to set `updated_at`, `recovery_note`, or `_checkpoint` by hand — the harness manages them. `requires_human_gate` is the one field you may add yourself if you're running the premium edition.

**Error handling:** validation runs on startup, right before the dependency-graph check (schema errors are checked first, since a missing `id` field would otherwise crash the dependency check). If errors are found, they're printed as a panel in the terminal and logged to `progress/harness.log` — same non-fatal pattern as the dependency-graph check below. The harness still starts; fix `feature_list.json` and restart.

If you add a new field to `feature_list.json` (in core code or a premium plugin), add it to `FeatureSchema` and bump `FEATURE_SCHEMA_VERSION` — otherwise every feature using it will fail validation as an unrecognized field.

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
| `/verbosity [summary\|normal\|verbose]` | Shows or changes the active console verbosity tier for the rest of the session. See [Console verbosity](#console-verbosity) |
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
| `SANDBOX_MODE` | `docker` | Where `run_bash` executes — `docker` (isolated container, recommended) or `local` (direct on host). See [Sandboxed execution](#sandboxed-execution) |
| `SANDBOX_NETWORK_MODE` | `egress-proxy` | Container network mode — `egress-proxy` (default-deny allowlist, most secure), `bridge` (full outbound, opt out), or `none` (fully air-gapped) |
| `SANDBOX_EGRESS_ALLOWLIST` | *(registries)* | Comma-separated hostnames reachable in `egress-proxy` mode; `*.example.com` matches subdomains too. See [Sandboxed execution](#sandboxed-execution) |
| `STRUCTURED_LOG_STDOUT` | `false` | Emit structured JSON logs to stdout, in addition to `progress/harness.log`. Off by default so it doesn't interleave with the Rich panels meant for a human at the terminal. Set to `true` to opt in. See [Structured logging](#structured-logging) |
| `HARNESS_VERBOSITY` | `normal` | Console output tier: `summary` \| `normal` \| `verbose`. See [Console verbosity](#console-verbosity) |

### Structured logging

The harness logs to two destinations at once — nothing you had before is removed:

1. **`progress/harness.log`** — plain text, unchanged (`%(asctime)s | %(levelname)s | %(message)s`). Anything already tailing this file, or any plugin that just calls `logging.getLogger(...).info(...)` and expects a configured root logger, keeps working exactly as before.
2. **stdout** — one JSON object per line, for log aggregators or structured-logging pipelines. **Off by default** — a human at the terminal is watching the Rich panels described in [Console verbosity](#console-verbosity) below, and a raw JSON line per log event interleaved with those panels is noise for that audience:

```json
{"timestamp": "2026-06-30T18:04:12.501Z", "level": "INFO", "session_id": "3f1e2b9a-...", "feature_id": 3, "message": "[IMPLEMENTER] SPAWN feature=3 attempt=1"}
```

| Field | Description |
|---|---|
| `timestamp` | UTC, ISO 8601, millisecond precision |
| `level` | `INFO` \| `WARNING` \| `ERROR` |
| `session_id` | UUID generated once per harness process — the correlation ID for every log line in that run |
| `feature_id` | The feature currently being processed by `run_feature_cycle()`, or `null` outside that scope (e.g. leader-level orchestration) |
| `message` | Same text that goes to `progress/harness.log` |

Both handlers are fed by the same `logging` calls, so this applies to any plugin's own logging too, with zero changes on the plugin side — `logging.getLogger(__name__).warning(...)` in a plugin shows up in both `progress/harness.log` and the JSON stdout stream automatically, since both are handlers on the root logger.

Set `STRUCTURED_LOG_STDOUT=true` in `.env` to opt in — for CI, or when piping this process's stdout to a log aggregator (Vector, Fluent Bit, etc.) that wants the machine-readable stream instead: `STRUCTURED_LOG_STDOUT=true python3 harness.py | jq 'select(.level == "ERROR")'`. `progress/harness.log` is unaffected either way.

### Console verbosity

Three tiers, ascending, controlling how much lands on the terminal (`HARNESS_VERBOSITY` in `.env`, default `normal`):

| Tier | Shows |
|---|---|
| `summary` | Feature start, final verdict (approved/rejected), session cost summary. Nothing else. |
| `normal` (default) | Everything in `summary`, plus one line per agent step — spec written, impl done, E2E result, review verdict, retry/skip notices. |
| `verbose` | Everything in `normal`, plus per-tool-call detail inside each agent's own loop (which tool, what arguments, what result). |

Warnings and errors always print regardless of tier — those are anomalies, not routine progress chatter, so `summary` mode doesn't hide them.

Change it for the session in `.env`, or live during a run with the `/verbosity` REPL command:

```
You → /verbosity
  Verbosity: normal
You → /verbosity summary
  Verbosity set to summary
```

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

> **Cost note:** cost is priced per model, not with a single global rate — see [Costs](#costs) below for how `MODEL_PRICING` works and what to do if you add a model that isn't listed there.

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

## Sandboxed execution

Agent-issued shell commands (`run_bash` — used to install deps, run tests, start
servers, etc.) execute inside an isolated Docker container **by default**. This
closes a real gap that existed before: `write_file`/`append_file` always respected
`SAFE_WRITE_DIRS`, but `run_bash` did not — a command like `echo … > /etc/hosts` or
`cat ~/.ssh/id_rsa` would run with your full user privileges. Filesystem confinement
is now enforced at the OS/mount-namespace boundary instead of by parsing commands in
Python: the container simply cannot see anything outside what's mounted.

```env
SANDBOX_MODE=docker   # default — isolate run_bash in a locked-down container
SANDBOX_MODE=local    # opt out — run directly on the host (clearly less safe)
```

**What the container gets:**
- The project mounted read-only at `/workspace`, with each `SAFE_WRITE_DIRS` entry
  re-mounted read-write on top — agents can write where they're supposed to and
  nowhere else
- Read-only root filesystem, non-root user, all Linux capabilities dropped
- Memory / CPU / process-count limits (`SANDBOX_MEM_LIMIT`, `SANDBOX_CPU_LIMIT`,
  `SANDBOX_PIDS_LIMIT`)
- A wall-clock kill switch independent of the command's own `timeout` — a runaway
  loop cannot outlive the container
- **Default-deny network egress** — see below

### Network egress: default-deny allowlist

By default (`SANDBOX_NETWORK_MODE=egress-proxy`), sandboxed containers are attached
only to an *internal* Docker network with **no route to the internet at all**. The
only way out is a small forward-proxy container (`egress_proxy.py`, built from
`Dockerfile.proxy`) that is the single thing on that network also connected to the
default bridge. It tunnels traffic through to its destination only when the
hostname matches `SANDBOX_EGRESS_ALLOWLIST` — everything else gets a 403, and a
tool that ignores the proxy env vars simply has no route and fails closed anyway.
This is enforced at the network boundary, not by guessing what an LLM-generated
command might try.

No TLS interception happens — for HTTPS the proxy reads only the plaintext
`CONNECT host:port` request line, checks the hostname, and (if allowed) tunnels
the encrypted bytes through opaquely. It never sees certificates or payloads.

```env
SANDBOX_NETWORK_MODE=egress-proxy   # default — default-deny allowlist (most secure)
SANDBOX_NETWORK_MODE=bridge         # opt out — full outbound access, same as the host
SANDBOX_NETWORK_MODE=none           # fully air-gapped — no network at all
```

```env
# Optional — comma-separated; "*.example.com" matches the bare domain + subdomains.
# The default covers the package registries and git hosts most features need
# (pypi, npmjs, yarnpkg, github + githubusercontent, nodejs/nodesource, debian).
SANDBOX_EGRESS_ALLOWLIST=pypi.org,files.pythonhosted.org,registry.npmjs.org,...
```

If the proxy can't be started for any reason (e.g. the daemon won't allow internal
networks), the harness falls back to `SANDBOX_NETWORK_MODE=none` — air-gapped —
rather than silently opening egress, with a printed warning explaining why and how
to opt into `bridge` instead. That keeps the safest-by-default posture: a broken
proxy never quietly becomes an open door.

**Prerequisites:** a Docker daemon. On macOS/Windows that means Docker Desktop,
[OrbStack](https://orbstack.dev) (free for commercial use, fastest startup — our
recommendation), or [Colima](https://github.com/abiosoft/colima) (CLI-only, fully
open source). `bash init.sh` detects whichever you have, builds both the sandbox
image (from `Dockerfile`) and — when `SANDBOX_NETWORK_MODE=egress-proxy` — the
egress proxy image (from `Dockerfile.proxy`) automatically, and offers a
`brew install` command for whichever runtime is missing.

**No Docker available?** The harness still runs — it falls back to `local` mode
with a one-time warning so you always know which mode you're in. You can also set
`SANDBOX_MODE=local` explicitly to silence that warning if you've made a deliberate
choice to run unsandboxed (e.g. inside a CI container that's already isolated).

```env
# Optional tuning — sensible defaults are baked in
SANDBOX_IMAGE=harness-sandbox:latest
SANDBOX_MEM_LIMIT=1g
SANDBOX_CPU_LIMIT=2
SANDBOX_PIDS_LIMIT=256
```

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

### Structured status files

Each agent's prose report (`progress/spec_N.md`, `impl_N.md`, `review_N.md`, `e2e_N.md`) is paired with a small sibling JSON file — `progress/impl_N.json` next to `progress/impl_N.md`, and so on — with a minimal machine-readable summary of the same outcome:

```json
{"schema_version": 1, "status": "done", "tests_passed": true, "files_touched": ["src/models/user.py", "tests/test_user.py"], "reason": null}
```

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | int | Currently `1` |
| `status` | string | Role-specific: `"ok"` (spec_writer), `"done"` (implementer), `"approved"`/`"rejected"` (reviewer), `"passed"`/`"failed"` (e2e_tester) |
| `tests_passed` | bool \| null | `null` for spec_writer (not applicable); the actual test-run outcome for the other three |
| `files_touched` | string[] | Files created/modified (implementer, and planned by spec_writer); `[]` for reviewer/e2e_tester |
| `reason` | string \| null | Short reason on rejection/failure; `null` otherwise |

**Why a sibling file instead of a block inside the `.md`:** spec_writer's own template requires documenting example response shapes (e.g. `{data, total, page, page_size}`) directly in the prose, and impl/review reports embed raw pytest output — both can legitimately contain JSON-looking text, so extracting "the" JSON block from freeform Markdown would be ambiguous. A separate file has no such collision risk and needs no parsing beyond `json.load()`.

**What this replaces:** the harness used to decide pass/fail and cache-reuse purely by pattern-matching the prose — `_verdict_is()` prefix-matching a returned "APPROVED"/"REJECTED: ..." chat string, and `"passed" in content` on `impl_N.md`. That last one had a live bug: pytest output containing `"2 failed, 1 passed"` matched `"passed" in content` and could incorrectly reuse a failing implementation. `_reviewer_verdict()`, `_e2e_verdict()`, and the implementer's cache check now read the structured `tests_passed`/`status` fields first.

**Backward compatible by design:** if the sibling `.json` is missing (e.g. a `progress/` directory from before this schema existed), every one of these call sites falls back to the exact old substring behavior — no migration needed, nothing crashes, old projects keep working exactly as before.

---

## Costs

Token usage and cost are tracked per agent. Run `/costs` in the REPL or check `progress/session_costs.json` after a session.

Typical cost per feature: **~$0.05–0.15 USD** with DeepSeek v4-pro depending on complexity.

### Per-model pricing

Cost is computed with `MODEL_PRICING`, a dict in `harness.py` mapping each model name to `{input_price, output_price}` (USD per token). `_track_usage` prices every API call using the model that actually generated that specific response — not a single global rate — so cost stays accurate when:

- **Roles use different models**, via `MODEL_BY_ROLE` (e.g. `leader`/`implementer` on `deepseek-v4-pro`, `spec_writer`/`reviewer`/`e2e_tester` on the cheaper `deepseek-v4-flash`).
- **A call falls back to a different provider mid-session**, via `LLM_FALLBACK_CHAIN` + `LLM_MODEL_MAP` (e.g. a DeepSeek outage routes a call to `gpt-4o`) — the API response's own `model` field is what gets priced, reflecting the model that was actually billed, not the one originally requested.

Any model not listed in `MODEL_PRICING` falls back to `deepseek-v4-pro` pricing (`_DEFAULT_PRICING_MODEL`), with a warning logged to `progress/harness.log` the first time that happens in a session — once per unknown model, not on every call. If you add a model to `MODEL_BY_ROLE`, `LLM_MODEL_MAP`, or `LLM_FALLBACK_CHAIN` that isn't already in `MODEL_PRICING`, add a pricing entry for it so cost reporting stays exact instead of approximate.

Per-role breakdown in `progress/session_costs.json` (`by_role.<role>.cost_usd`) and the totals are both computed this way — token counts were always exact, and cost now is too, even in mixed-model/mixed-provider runs.

---

## Roadmap

Active development continues in the premium edition. See the [⭐ Premium modules](#-premium-modules) section to learn about upcoming capabilities or get access.


---

## Changelog

### v1.21.0
- **`STRUCTURED_LOG_STDOUT` defaults to `false`; new `HARNESS_VERBOSITY` console tiers, replacing `VERBOSE`.** With structured JSON logging defaulting on, every one of `_log()`'s ~50 call sites also emitted a raw JSON line on stdout — interleaved with the Rich panels meant for a human at the terminal, two audiences fighting for one screen. Flipped the default off (opt in with `STRUCTURED_LOG_STDOUT=true` for CI/log aggregators); extracted `_structured_log_stdout_enabled()` so the default is unit-testable without going through actual (process-global, only-configures-once) handler registration. Separately: the old `VERBOSE` flag (hardcoded `True`) already gated *tool-call-level* detail inside `run_agent`'s and `run_leader`'s loops, but the *per-agent* lines (spec/impl/e2e/review one-liners, retry/skip panels) had no gate at all — always printed, no way to quiet them, and no "just tell me when a feature starts and finishes" tier existed at all. Replaced `VERBOSE` with `HARNESS_VERBOSITY=summary|normal|verbose` (`.env` default, or change live with the new `/verbosity` REPL command) and a thin `_vprint(min_level, ...)` wrapper around `console.print` — `_verbosity_at_least()` is the single place tier-comparison logic lives, so none of the ~20 migrated call sites duplicate it. `summary` is genuinely new: added a "▶ Feature #N started" line and "✅/❌ approved/failed" verdict lines to `_run_feature_cycle_impl`, since nothing at that granularity existed before. Warnings/errors from `_log()` always print regardless of tier — anomalies, not routine chatter. A larger fourth idea (buffering each feature's output into one grouped panel, using the existing `_CURRENT_FEATURE_ID` contextvar) was investigated and deliberately deferred: it's cheap to build on top of the `_vprint` funnel this adds, but premium's `harness_parallel.py` documents that it currently relies on immediate, interleaved `console.print` from concurrent threads — changing that shared behavior isn't a call the public repo should make unilaterally without premium verifying it first. 29 new tests in `tests/test_harness_core.py` (`TestStructuredLogStdoutDefault`, `TestVerbosity`, `TestFeatureCycleVerbosityIntegration`), including an end-to-end regression proving `summary` mode suppresses a retry panel that `normal` mode shows. See [Structured logging](#structured-logging) and the new [Console verbosity](#console-verbosity) section.

### v1.20.0
- **Structured sibling JSON status files, replacing prose-substring heuristics for cache/verdict decisions.** Pipeline-critical decisions were made by pattern-matching agent prose: `_verdict_is()` prefix-matching a returned "APPROVED"/"E2E_PASSED" chat string (already the source of two documented bugs — case-sensitive verdict parsing, E2E verdict polarity), and `spawn_implementer`'s cache check, `"passed" in content and "[ERROR" not in content` on `impl_N.md`, which had a live bug of the same kind: pytest output containing `"2 failed, 1 passed"` matches `"passed" in content` and could silently reuse a failing implementation. Each of the 4 agents now also writes a small sibling JSON file next to its prose report (`progress/impl_N.json` next to `impl_N.md`, etc.) with `{schema_version, status, tests_passed, files_touched, reason}`. New `_read_structured_status()` reads it (returns `None` — never raises — if absent, unreadable, or missing `schema_version`, so foreign JSON can't be half-trusted); new `_reviewer_verdict()` and `_e2e_verdict()` prefer it over `_verdict_is()` + prefix-stripping; `spawn_implementer`'s cache check prefers `tests_passed`/`status` over the substring check. A sibling file (not a block embedded in the `.md`) was chosen deliberately: `agents/spec_writer.py`'s own template requires documenting example response shapes like `{data, total, page, page_size}` directly in the prose, and impl/review reports embed raw pytest output — both can contain JSON-looking text, making "the" JSON block in a Markdown file ambiguous to extract; a separate file has no such collision risk. Every call site falls back to the exact old substring behavior when the sibling file is absent, so `progress/` directories from before this schema existed keep working with zero migration. See the new [Structured status files](#structured-status-files) section. 16 new tests in `tests/test_harness_core.py` (`TestReadStructuredStatus`, `TestImplCacheStructuredVsLegacy`, `TestReviewerAndE2eVerdict`), including a regression test reproducing the `"2 failed, 1 passed"` cache bug and confirming it's fixed for projects using the new schema (and unchanged, by design, for legacy `progress/` directories without it).

### v1.19.0
- **Structured JSON logging on stdout, alongside (not instead of) `progress/harness.log`.** The root logger previously had a single `logging.basicConfig(filename="progress/harness.log", ...)` handler — fine for humans tailing a file, unusable for a log aggregator, and with no correlation ID to tie a run's log lines together. Added a second handler: a `logging.StreamHandler(sys.stdout)` with a new `_JsonLogFormatter` that renders one JSON object per line (`timestamp`, `level`, `session_id`, `feature_id`, `message`). `progress/harness.log` and its exact plain-text format are untouched — this was additive by design, checked against `plugins/example_plugin.py` and the documented premium plugins first, since any plugin that just calls `logging.getLogger(...).info(...)` relies on the root logger already being configured; both handlers fire from the same call, so plugin log lines get the JSON treatment too with no plugin-side changes needed. `session_id` is a UUID generated once per harness process (`_SESSION_ID`). `feature_id` is populated via a `contextvars.ContextVar` (`_CURRENT_FEATURE_ID`) set for the duration of each feature's cycle — `run_feature_cycle()` became a thin wrapper around the renamed `_run_feature_cycle_impl()` that sets the var in a `try`/`finally` so it's reset even if the cycle raises; contextvars are per-thread, so this stays correct under the premium parallel-feature-execution plugin's `ThreadPoolExecutor`. New `STRUCTURED_LOG_STDOUT=false` env var (default on) disables the stdout handler without touching the file handler. The handler registration is guarded against duplicates (checked by handler name) so re-importing the module — e.g. across this project's own test suite — never piles up extra stdout handlers. See the new [Structured logging](#structured-logging) section. 7 new tests in `tests/test_harness_core.py::TestStructuredLogging`.

### v1.18.0
- **Versioned schema validation for `feature_list.json`.** Entries were parsed with no schema — every field access went through `dict.get(...)`, so a misspelled field like `"depnds_on"` instead of `"depends_on"` was silently ignored: no error, no dependency enforcement, just dead JSON sitting in the file. Added `FeatureSchema` (pydantic, `FEATURE_SCHEMA_VERSION = "1.0"`) in `harness.py`, with `extra="forbid"` so unknown fields are rejected instead of dropped. Required: `id`, `title`, `description`, `status` (validated against `tools.VALID_FEATURE_STATUSES`, the existing single source of truth, reused rather than duplicated). Optional with defaults: `e2e`, `depends_on`, `created_at`. Also allowlisted the fields the harness itself writes back into feature entries (`updated_at`, `recovery_note` from `recover_stale_features()`/`update_feature_status()`, `_checkpoint` from `_save_checkpoint()`) and the one field the premium **Human-in-the-loop gates** plugin sets (`requires_human_gate`) — none of those would have validated under a naive "only the README's table" schema. `_validate_feature_schema()` runs on startup in `main()`, right before `_validate_dependencies()` (schema errors are checked first, since a missing `id` would otherwise crash the dependency-graph check) — same non-fatal panel + log pattern as the existing circular-dependency check, never crashes the harness. See the new [Schema validation](#schema-validation) section. 9 new tests in `tests/test_harness_core.py::TestFeatureSchema`.

### v1.17.0
- **Per-model cost pricing, replacing the single global `_PRICE_INPUT`/`_PRICE_OUTPUT` constants.** Those two constants were calibrated for `deepseek-v4-pro` only, but `MODEL_BY_ROLE` already lets each role run a different model and `LLM_FALLBACK_CHAIN` already lets any role's calls land on a different provider mid-session — so reported cost in a mixed-model/mixed-provider run was silently approximate, with no indication anything was off. Replaced the two constants with `MODEL_PRICING`, a `model_name -> {input_price, output_price}` dict in `harness.py`, plus `_price_for_model()`, which falls back to `deepseek-v4-pro` pricing for any unlisted model and logs a warning the first time that happens per model per session (not on every call, to avoid log spam). `_track_usage` now takes the model that actually generated each response (`api_response.model`, which reflects any `LLM_MODEL_MAP` provider translation) and prices that specific call with it, instead of applying one rate to every token in the session; `_SESSION_COSTS` buckets now accumulate `cost_usd` directly per role rather than being recomputed from raw token totals at read time. See the updated [Costs](#costs) section. New tests in `tests/test_harness_core.py::TestPerModelPricing` cover a known model's own pricing being used, an unknown model falling back with a one-time warning, and a mixed run (three different models across three roles) reporting the correct per-role and total cost.

### v1.15.0
- **Implementer: React/Next.js hooks-order guard.** A generated component placed a conditional `if (...) return null;` between `useState` calls and later `useCallback`/`useEffect`/`useMemo` calls — syntactically valid and undetected by lint rules that only check for hooks inside loops/conditionals, but it changes how many hooks run between renders, so React throws `Error: Rendered more hooks than during the previous render` the instant the guard's condition flips (Next.js renders this as a full-page dev error overlay instead of the component). `agents/implementer.py`'s CONVENTIONS section gained a mandatory rule: every hook call must sit at the top of the component, before any early-return guard, even when the guard "logically belongs" right next to the data it checks (e.g. a role check right after `useAuth()`).
- **Implementer: raw SQL INSERT column completeness.** An endpoint's raw `INSERT INTO "<table>" (...)` only listed the columns present in the request's Pydantic schema, silently omitting a `NOT NULL UNIQUE` server-generated column (no default) that a sibling endpoint for the same table correctly generated (`uuid.uuid4()`) — every call then failed with a Postgres NOT NULL violation, which generic exception handling re-surfaced as a misleading 409 "conflict". New mandatory rule in `agents/implementer.py`: before finishing any raw SQL INSERT, read the table's full `CREATE TABLE` definition from the migration files (not just the request/response schema), supply a value for every NOT NULL/no-default column, and match an existing working INSERT into the same table if one already exists in the codebase.
- **E2E_TESTER: form-fill hydration race.** A Playwright test used `page.goto(url, wait_until="domcontentloaded")` then immediately `.fill()`'d a React-controlled input — `domcontentloaded` doesn't wait for `"use client"` hydration, so under Docker's slower first-hit compile, hydration can reset the input back to React's empty state after Playwright already filled it, failing with the browser's native "Please fill out this field" tooltip (looks like a wrong selector, not a timing race). `agents/e2e_tester.py` gained a new mandatory rule generalizing the existing login-only HYDRATION-SAFE NAVIGATION rule to every form fill on every page: use `wait_until="networkidle"` on goto, and after every `.fill()` verify the value actually stuck with `expect(locator).to_have_value(...)` (auto-retrying) instead of a fixed sleep or a blind retry loop.
- **`read_file` missing-path hint + FILE PATH VERIFICATION rule.** When an agent guessed a wrong file extension/spelling (e.g. `auth.ts` instead of the real `auth.tsx`), `read_file()` in `tools.py` returned only the raw `FileNotFoundError` message, giving the agent no way to self-correct — observed burning the full iteration budget retrying path variants without ever calling `list_files`. `read_file()` now catches `FileNotFoundError` specifically and returns a `hint` field listing the real files in the parent directory. All four agent prompts with `read_file` + `list_files` (`implementer.py`, `e2e_tester.py`, `reviewer.py`, `spec_writer.py`) gained a matching HARD RULES line: never guess a filename — use `list_files` first, and on any `read_file` error, read the `hint` field rather than retrying a guessed variant. Verified with a standalone script reproducing the exact scenario: the `hint` field correctly lists the real sibling files in the parent directory.

### v1.13.1
- **E2E_TESTER: don't unconditionally start the server.** PROTOCOL step 5 previously had the agent always try to start the backend/frontend itself with `run_bash` before testing. Companion fix to the premium "persistent E2E services" ADR (2026-06-18): the harness (or a premium plugin) may already have started the project's server before E2E_TESTER was even spawned, and under `SANDBOX_MODE=docker` a server started via `run_bash` runs in an ephemeral container with no published ports — Playwright running on the host could never reach it anyway. Step 5 now only falls back to starting the server manually when PRECOMPUTED CONTEXT explicitly says backend/frontend aren't responding.

### v1.14.0
- **Playwright Node config resolution.** `run_playwright_tests` for Node/@playwright/test projects now resolves the project's own `playwright.config.ts`/`.js` from the resolved `e2e_test_dir` and passes it explicitly via `--config` — relying on npx auto-discovery broke with "Requiring @playwright/test second time" when a repo had more than one config/spec tree (e.g. `e2e/` and `frontend/`).
- **`take_screenshot` uses `sys.executable`.** Hardcoded `python`/`python3` replaced with `sys.executable`, since neither name is guaranteed on every machine's PATH but `sys.executable` always matches the running interpreter.
- **Screenshot read tip points at `error-context.md`.** Both `run_playwright_tests` result "tip" fields previously suggested calling `read_file` on the failure screenshot — a binary PNG, which the text-only `read_file` tool errors on (and this harness's LLM provider has no vision input anyway). Now point at `error-context.md` in the matching `test-results/<test-name>/` subfolder instead.
- **E2E_TESTER: sandbox has no route to the live app.** New first HARD RULE in `agents/e2e_tester.py`: `run_bash` executes in an isolated sandbox with no network route to the host — a failed curl/ping there never means the host-started backend/frontend are actually down. Lives in `SYSTEM_PROMPT` (resent on every call) so it survives context compaction, unlike a one-time task injection.
- **`MAX_ITER_AGENT` configurable via `.env`.** Hardcoded `30` → `int(os.getenv("MAX_ITER_AGENT", "30"))`, matching the existing `MAX_ITER_SPEC` pattern — lets a project's `.env` raise the budget for features needing more diagnose-fix-rerun cycles.
- **`load_dotenv(override=True)`.** A stale shell-exported env var from an earlier session was silently beating a freshly-edited `.env` value.

### v1.13.0
- **Case-insensitive verdict parsing.** `result.strip().startswith("APPROVED")` / `"E2E_PASSED"` / `"E2E_FAILED"` were exact-prefix, case-sensitive checks — an LLM verdict like "Approved." or "approved" was silently read as a rejection (wasting a full retry), and conversely a lowercase "e2e_failed" would have been silently read as a PASS. New `_verdict_is(result, marker)` helper in `harness.py` does a case- and punctuation-tolerant prefix compare; all 5 verdict call sites now use it. Verified with 10 hand-written cases (pure string function, no live LLM needed).
- **E2E verdict polarity bug.** Separate from the case-sensitivity fix above: `run_feature_cycle`'s E2E step only rejected when the result started with `"E2E_FAILED"` — anything else, including a `MAX_ITER` timeout error string, fell through as an implicit pass and reached the Reviewer with no real E2E evidence (14 occurrences in one project's log before this fix). Switched from denylist to allowlist: `e2e_passed = _verdict_is(e2e_result, "E2E_PASSED")` — only that explicit marker counts as a pass. Verified with 7 hand-written cases.
- **Scope rule made visible to the Spec Writer.** The "max 4-5 files per feature" rule lived only as a `harness.py` comment, never injected into any agent prompt. Added a `SCOPE RULE` section to `agents/spec_writer.py`: if the file list would exceed ~4-5 files, the spec still gets written, but with a `## ⚠ Scope warning` section recommending the feature be split via `depends_on`.
- **`e2e_tester.py` path-convention ambiguity.** The prompt said `/workspace/` is read-only inside `run_bash`, but never clarified that `read_file`/`write_file`/`list_files`/`append_file` run on the host with paths relative to the WORKING DIRECTORY, not under `/workspace/`. Added an explicit PATH CONVENTION line to HARD RULES.

### v1.12.0
- **Search-tool hallucination guardrail** — agents (especially E2E_TESTER) occasionally hallucinate a `grep`/`search`/`find` tool that doesn't exist in this harness (only `run_bash` can search file contents), which previously burned the whole iteration budget retrying the same failing call with no recovery path. `tools.py`'s `execute_tool()` now recognizes common search-tool aliases (`grep`, `rg`, `search`, `search_files`, `find`, `glob`, `ripgrep`) in its "tool not found" error and appends a hint pointing the agent at `run_bash("grep -rn 'pattern' path/")` instead, so it can self-correct within budget rather than repeating the dead end. All four agent prompts that have `run_bash` (`e2e_tester.py`, `implementer.py`, `reviewer.py`, `spec_writer.py`) also gained an explicit HARD RULES line stating there is no dedicated search tool. Purely additive — unrelated "tool not found" errors are unaffected. 2 new tests in `tests/test_harness_core.py`; full suite (88 tests) passes.

### v1.11.0
- **Node/@playwright/test support for E2E testing** — fixes E2E_TESTER and `run_playwright_tests` being hardcoded to Python/pytest-playwright, which broke on projects whose E2E suite is a Node/@playwright/test project (e.g. `e2e/*.spec.ts` with its own `playwright.config.ts`). `stack_profiles.json` gains a `playwright-node` entry alongside the existing `playwright` (Python) one; `stack_layout.py`'s `resolve_layout()` now also resolves `e2e_runtime`/`e2e_test_dir`/`e2e_file_ext`/`e2e_run_cmd`/`e2e_notes`/`e2e_key` following the same env-var (`STACK_E2E`) > `stack_config.json` (`"e2e_runner"`) > profile-defaults > hardcoded-fallback precedence already used for backend/frontend. `run_playwright_tests` in `tools.py` is now a thin dispatcher that branches to a Python (pytest) or Node (`npx playwright test`) runner based on the resolved stack instead of always shelling out to pytest. The E2E_TESTER system prompt (`agents/e2e_tester.py`) no longer hardcodes `tests/e2e/` + `.py` everywhere — it now points at "the E2E test directory and file convention given under STACK COMMANDS" with both Python and Node examples. Fully backward-compatible: with no stack configuration the default remains Python/pytest-playwright, identical to pre-v1.11.0 behavior. 11 new tests (`tests/test_stack_layout.py`, plus 4 new cases in `tests/test_harness_core.py`); full suite (86 tests) passes.

### v1.10.1
- **`<app>` placeholder in stack profiles was never substituted.** `python-django`'s `safe_write_dirs`/`code_tree_dirs`/`dirs` use a generic `<app>` placeholder (no fixed source-root convention like FastAPI's `src/`), but `resolve_layout()` copied profile values verbatim — `_is_safe_path()` ended up doing `startswith("<app>/")` against real paths like `myproject/models.py`, rejecting every write to the actual Django app directory while only the literal, impossible `<app>/models.py` path passed. Fixed in `stack_layout.py`: `resolve_layout()` now resolves `app_name` (`APP_NAME` env → `stack_config.json`'s `app_name` → default `"app"`) and substitutes `<app>` → `app_name` in `safe_write_dirs`, `code_tree_dirs`, and `dirs` via a new `_substitute_placeholder()` helper. No-op for every other profile (none of them use the placeholder).

### v1.10.0
- **Harness self-quality / CI** — unit-test coverage for core harness logic with no live API calls. New `tests/test_harness_core.py` covers: `_topological_sort` (linear chains, diamond DAG, cycle detection, partial cycles, disconnected roots), `_validate_dependencies` (self-dep, missing dep, circular dep), `_read_feature_list_raw` / `_write_feature_list_raw` (roundtrip, missing file, corrupt JSON), budget enforcement (`_track_usage` sets `_BUDGET_EXCEEDED`; `run_feature_cycle` skips when exceeded; zero-budget disables enforcement), and `tools.py` (`_is_safe_path` with path-traversal blocking, `update_feature_status` for valid/invalid/unknown, `execute_tool` dispatch). Companion files: `.github/workflows/ci.yml` (GitHub Actions — pytest + ruff + mypy on push/PR to main, Python 3.9) and `pyproject.toml` (ruff config: `py39` target, E/W/F/I rules; mypy config: `ignore_missing_imports = true`). 30 new tests; full suite passes in < 1 s.

### v1.9.0
- **Durable state / resumability** — the harness now survives mid-feature crashes without restarting from scratch. Three new helpers (`_save_checkpoint`, `_load_checkpoint`, `_clear_checkpoint`) persist a `_checkpoint` field directly into `feature_list.json` after each major step (`spec_done`, `impl_done`, `e2e_done`). On restart, `run_feature_cycle` reads the checkpoint and skips any already-completed steps, resuming from the next one; the start attempt is also restored so retry-budget accounting is correct. `recover_stale_features` preserves the checkpoint when resetting `in_progress → pending` so a crash during, say, implementation doesn't throw away a completed spec. Checkpoints are cleared on approval, on final failure, and on FATAL implementation errors. Zero configuration required; no new dependencies. 19 tests in `tests/test_resumability.py`.

### v1.8.0
- **LLM provider resilience** — automatic fallback chain across OpenAI-compatible providers. New `_Provider` class and `_build_provider_chain()` replace the single hardcoded `client`; `_call_api_with_fallback()` encapsulates all retry + provider-switch logic and is used by every API call site (`run_agent`, leader loop, context compaction, spec validation). `_classify_error` gains a new `PROVIDER_FAILURE` class (401, 403, 529, auth/capacity errors) that skips remaining retries and advances to the next provider immediately, distinct from `TRANSIENT` (rate limit/timeout — retries on same provider) and `FATAL`. Configuration via `.env`: `LLM_FALLBACK_CHAIN` (e.g. `deepseek,openai,groq`), per-provider `*_API_KEY` / `*_BASE_URL`, and `LLM_MODEL_MAP` (JSON dict for translating model names across providers). Built-in providers: `deepseek`, `openai`, `groq`, `custom`. Zero breaking changes — without `LLM_FALLBACK_CHAIN` the behavior is identical to v1.7.0. 26 tests in `tests/test_llm_resilience.py`.

### v1.7.0
- **Default-deny network egress for the sandbox** — closes the "Network egress policy" gap from the architecture review. New default `SANDBOX_NETWORK_MODE=egress-proxy`: sandboxed containers attach only to an internal Docker network with no route to the internet; the only way out is a small forward-proxy container (`egress_proxy.py`, built from new `Dockerfile.proxy`) that tunnels traffic on only when the destination hostname matches `SANDBOX_EGRESS_ALLOWLIST` (default covers pypi/npm/yarn/github/nodejs/debian) — everything else gets a 403, enforced at the network boundary so a tool that ignores the `*_PROXY` env vars simply has no route either way. No TLS interception — HTTPS is tunneled opaquely once the `CONNECT` hostname passes the allowlist check. `bridge` (full outbound, opt-out) and `none` (fully air-gapped) remain available via `SANDBOX_NETWORK_MODE`. If the proxy can't start, the harness falls back to `none` rather than silently opening egress, with a printed warning. `sandbox.py` gained `_ensure_proxy*` lifecycle methods (internal network + proxy container, reused across runs); `init.sh` builds `harness-egress-proxy:latest` alongside the sandbox image. See [Sandboxed execution](#sandboxed-execution)

### v1.6.0
- **Sandboxed execution** — `run_bash` now executes inside a locked-down Docker container by default (`SANDBOX_MODE=docker`); new `sandbox.py` module (`SandboxRunner` interface, `LocalSubprocessRunner`, `DockerSandboxRunner`) preserves `run_bash`'s exact signature/return shape so the rest of the pipeline is unaffected. Closes the long-standing write-confinement bypass — `SAFE_WRITE_DIRS` is now enforced at the OS/mount-namespace boundary (read-only project mount with rw remounts per safe dir) instead of by string-matching commands. Adds a non-root user, dropped capabilities, read-only rootfs, memory/CPU/PID limits, and a wall-clock kill switch independent of the per-command timeout. Falls back to `SANDBOX_MODE=local` (today's pre-sandbox behavior) with a one-time warning when no Docker daemon is reachable. New `Dockerfile` ships the sandbox image (Python 3.11 + Node 18 + project deps); `init.sh` detects Docker Desktop/OrbStack/Colima, builds the image, and offers a `brew install` for whichever is missing. See [Sandboxed execution](#sandboxed-execution)

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

## ⭐ Premium modules

The following capabilities are available in the **multi-agent-harness-premium** edition, built on top of this open-source core. All modules are implemented as plugins or alternative entry points — they never modify the base harness, so your public fork stays clean and mergeable.

| Module | What it does |
|---|---|
| **Parallel feature execution** | Dependency-aware `ThreadPoolExecutor` scheduler. Features with no inter-dependencies run concurrently; features with `depends_on` wait for their level to complete. Thread-safe file locks and cost tracking included. |
| **RAG for legacy code** | Indexes an existing codebase into a local vector store (ChromaDB + sentence-transformers). Injects relevant existing code snippets into each spec before implementation — reduces hallucinations and duplicate logic when extending legacy systems. |
| **Agent memory across features** | Distills each approved feature into typed memory entries (conventions, decisions, patterns, ADRs) and injects the most recent ones into subsequent specs — agents on feature #10 know what agents on feature #1 decided, across restarts. |
| **SDLC governance** | Hard SAST gate (Bandit) that vetoes approvals before they are committed, plus automatic Git branch creation, conventional commit, and GitHub PR opening on every approved feature. Draft PRs on high-severity findings. |
| **Human-in-the-loop gates** | `"requires_human_gate": true` on any feature pauses the pipeline before the Spec Writer runs, blocking until a human approves interactively or via the REST API (`HITL_HTTP_MODE=true`). Decisions persist across restarts so each feature is only ever gated once. |
| **Pluggable storage backends** | Abstract storage interface with three drivers: `json` (default, zero overhead), `sqlite` (single-file, no server), `postgres` (PostgreSQL via psycopg2). Select via `STORAGE_BACKEND` env var. Thread-safe; survives process restarts automatically. |
| **Service API** | FastAPI REST + SSE server. Submit runs (`POST /runs`), stream live events (`GET /runs/{id}/stream`), resolve HITL gates via HTTP (`POST /gates/{id}/approve`), and fetch generated artifacts — all without a human at the terminal. CI/CD and orchestrator friendly. |
| **Quality evals** | Fixture-based regression suite. Define golden feature descriptions with expected outcomes; run them through the harness in isolated temp directories; compare against a baseline to detect regressions from prompt or model changes. Exits `1` on any failure or regression — ready for CI gates. |
| **Stack parametrization** | Build applications in any technology stack — not just the default FastAPI + React. Configure backend (FastAPI, Django, .NET Core, Spring Boot, Express, NestJS, Gin, Rails, Laravel), frontend (React, Angular, Vue, Next.js, Nuxt, SvelteKit, or none), database (JSON, PostgreSQL, MySQL, SQLite, MongoDB), and e2e runner (Playwright, Cypress, or none) via a single `stack_config.json`. Every agent receives the correct architecture context, conventions, and CLI commands for the chosen stack — zero configuration required to keep using the defaults. |

Premium is distributed as a private fork.

**Contact us to learn more or request access:**

- ✉ [felipe.mejia@vora.software](mailto:felipe.mejia@vora.software)
- 🌐 [vora.software](https://vora.software)

---

## License

MIT
