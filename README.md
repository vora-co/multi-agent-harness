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
    🔍 Reviewer      — validates tests pass and approves or rejects
         ↓
    🧪 E2E Tester    — runs Playwright browser tests (only if e2e: true)
         ↓
    ✅ Feature marked done — Leader moves to the next one
```

Review runs before E2E — the cheap, purely-static check happens before the most expensive step in the cycle (E2E force-recreates the environment, does a cold compile, and drives a real browser), so an ordinary reviewer rejection never wastes a full Playwright cycle it never needed.

If the Reviewer rejects, the Implementer retries with the rejection reason injected. If E2E fails after an approved review, the Implementer also retries — and the fix is re-reviewed before E2E runs again. The harness retries up to `MAX_RETRIES_REVIEW` times before marking a feature as `failed`.

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
| `process all pending features` | Processes all pending features in order (via the Leader-LLM) |
| `run only feature 3 and stop` | Processes only feature #3 (via the Leader-LLM) |
| `process features 2 and 3` | Processes a specific range (via the Leader-LLM) |
| `/auto` | Deterministic, code-level equivalent of "process all pending features" — no LLM orchestration, no `MAX_ITER_LEADER` ceiling. See [Deterministic batch driver](#deterministic-batch-driver) |
| `/auto <id>` | Runs just feature `<id>` (must be `pending`) the same way |
| `/features` | Shows the status of all features |
| `/costs` | Shows token usage and estimated cost for this session, plus a per-feature cost breakdown table (highest-spend first) |
| `/budget` | Shows current spend vs. budget limit with a progress bar |
| `/status` | Shows the current state (progress/current.md) |
| `/verbosity [summary\|normal\|verbose]` | Shows or changes the active console verbosity tier for the rest of the session. See [Console verbosity](#console-verbosity) |
| `/cache` | Shows LLM response cache status — enabled/disabled, entries on disk, hits and estimated savings this session. See [LLM response cache](#llm-response-cache) |
| `/cache clear` | Deletes all on-disk cache entries |
| `/quit` | Exits the harness |

---

## Deterministic batch driver

`run_all_pending()` (the `/auto` REPL command) is a code-level alternative to the Leader-LLM for the one thing it does most often: process every `"pending"` feature in `depends_on` order. It reads `feature_list.json`, sorts with the same `_topological_sort()` used for startup validation, filters down to `"pending"`, and calls `run_feature_cycle()` for each in turn — updating `status` and writing `progress/current.md`/`progress/history.md` with a fixed template instead of Leader-composed prose.

It does not replace the Leader-LLM — natural-language requests ("only run feature 3 and the ones after it", ad-hoc questions) still go through it via the REPL's default input path. `/auto` is a parallel, zero-inference path for the common case, with no `MAX_ITER_LEADER` ceiling forcing a human re-prompt mid-batch.

Stops on: an empty pending queue, the session budget being exhausted (the about-to-run feature is left `"pending"`, not marked `"failed"`, so a later session can pick it up), or structural `feature_list.json` dependency errors. `/auto <id>` runs just one feature (must be `"pending"`). A Human-in-the-loop gate plugin, if one is loaded, already intercepts inside `run_feature_cycle()` itself — nothing `/auto`-specific needed.

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
| `FEATURE_BUDGET_USD` | `0` | Max USD spend on a *single* feature (across all its retries), independent of `COST_BUDGET_USD`. `0` disables enforcement. Example: `FEATURE_BUDGET_USD=0.50` |
| `MAX_ITER_LEADER` | `30` | Max iterations for the Leader agent |
| `MAX_ITER_IMPL` | `50` | Max iterations for the Implementer |
| `MAX_ITER_REVIEWER` | `40` | Max iterations for the Reviewer |
| `MAX_RETRIES_API` | `3` | Retries on transient API errors (rate limit, timeout) |
| `MAX_RETRIES_IMPL` | `3` | Times the Implementer can retry a feature |
| `MAX_RETRIES_REVIEW` | `2` | Times the impl→review cycle retries before marking failed |
| `CONVERGENCE_STREAK_LIMIT` | `7` | Consecutive `run_agent` iterations with no write before a "stop exploring, make the edit" nudge is injected (fires again every N iterations if the streak continues; from the second firing on, the text escalates to an imperative "your NEXT tool call MUST be write_file"). `0` disables it |
| `MAX_ITER_WITHOUT_WRITE` | `40` | Hard companion to the soft nudge above: abort the attempt early once it reaches this many total iterations with **zero** writes, with a message distinct from a normal max_iter cutoff. The investigation digest still gets written, so the retry starts informed with the remaining budget unspent. `0` disables it |
| `REPRO_SCRIPT_TIMEOUT_S` | `180` | Timeout ceiling for `run_repro_script` (the host-side runner for `progress/repro_<id>.py`/`.sh`, exposed to the implementer only on e2e-enabled bug-fix features). Also caps any agent-supplied timeout |
| `DESTRUCTIVE_SHRINK_RATIO` | `0.30` | `write_file` flags a rewrite of an existing source file (`.py .js .jsx .ts .tsx .mjs .cjs .go .rb .php .java .cs`) that shrinks it by more than this fraction (non-blocking `warning` in the tool result naming what was removed). `0` disables the shrink check; the removed-symbols check (Python/JS/TS only) always runs. See the v1.50.0/v1.51.0 changelog entries |
| `DESTRUCTIVE_SHRINK_MIN_LINES` | `40` | The shrink check above only applies when the existing file exceeds this many lines — a 10-line file dropping to 6 is a legitimate refactor, and noise trains agents to ignore warnings. The removed-symbols check has no floor |
| `SANDBOX_MODE` | `docker` | Where `run_bash` executes — `docker` (isolated container, recommended) or `local` (direct on host). See [Sandboxed execution](#sandboxed-execution) |
| `SANDBOX_NETWORK_MODE` | `egress-proxy` | Container network mode — `egress-proxy` (default-deny allowlist, most secure), `bridge` (full outbound, opt out), or `none` (fully air-gapped) |
| `SANDBOX_EGRESS_ALLOWLIST` | *(registries)* | Comma-separated hostnames reachable in `egress-proxy` mode; `*.example.com` matches subdomains too. See [Sandboxed execution](#sandboxed-execution) |
| `STRUCTURED_LOG_STDOUT` | `false` | Emit structured JSON logs to stdout, in addition to `progress/harness.log`. Off by default so it doesn't interleave with the Rich panels meant for a human at the terminal. Set to `true` to opt in. See [Structured logging](#structured-logging) |
| `HARNESS_VERBOSITY` | `normal` | Console output tier: `summary` \| `normal` \| `verbose`. See [Console verbosity](#console-verbosity) |
| `LLM_CACHE_ENABLED` | `false` | Opt-in on-disk cache for chat-completion calls, keyed on the exact (resolved model, messages, tools) tuple. Off by default. See [LLM response cache](#llm-response-cache) |
| `LLM_CACHE_DIR` | `progress/.llm_cache` | Where cache entries are written, one JSON file per hash. Harness-internal — not a directory agents are ever instructed to touch |

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

## Secrets protection

`.env` holds `DEEPSEEK_API_KEY` and every other provider credential — three independent layers keep it (and the values it holds) out of agent hands and out of logs:

1. **Agents can never read `.env`.** Unlike `write_file`/`append_file`, `read_file` has no directory confinement (agents need to read source files anywhere), so nothing stopped `read_file(".env")` before this. `read_file` now refuses any path matching `.env`, `.env.local`, `.env.production`, etc., and `list_files` omits them from directory listings — an agent debugging an API connectivity issue can no longer stumble onto the key by reading the file that configures it.
2. **`SANDBOX_MODE=local` no longer leaks provider keys into shell commands.** This mode (opt-in, and the automatic fallback when Docker isn't available) runs agent shell commands as a direct subprocess of the harness process. Without an explicit `env=`, `subprocess.run()` inherits the *entire* host environment — so a command as ordinary as `env` or `printenv` would print `DEEPSEEK_API_KEY` straight into that command's output. The local runner now strips every `*_API_KEY` variable from the subprocess environment first. (Docker mode was never affected — it only ever passes an explicit, harness-controlled `environment=` dict into the container, never a copy of the host's.)
3. **Redaction as a last line of defense.** Every configured `*_API_KEY` value is captured once at startup. Tool results are redacted immediately after execution — before they're logged *or* appended to the LLM's own conversation history (so a leaked key can't resurface later in a `progress/*.md` report either) — and `_log()` redacts its own message text too. If some path not covered by 1 or 2 ever puts a raw key into a tool result or exception message, it still can't reach `progress/harness.log`, the JSON stdout stream, or an agent's own context: it appears as `***REDACTED***`.

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
| `after_feature_approved` | Review + E2E passed and the approval gate cleared; `feature_list.json` already shows the feature as `"done"` when this fires (v1.46.0) | `feature_id`, `description`, `attempts` |
| `after_feature_failed` | Feature exhausts all retries | `feature_id`, `description`, `attempts`, `final_verdict` |
| `after_reviewer_rejected` | Reviewer rejects a cycle (every attempt, including retried ones) | `feature_id`, `description`, `attempt`, `max_attempts`, `rejection_reason` |
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
| `premise_check` | `"failed"` \| `"passed"` \| absent | Implementer-only, optional: `"failed"` when the attempt ended via the sanctioned PREMISE CHECK EXIT (its direct verification refuted the spec's diagnosis — see the v1.52.0 changelog entry). Omitted entirely when not applicable |

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

### LLM response cache

**Opt-in, off by default.** `LLM_CACHE_ENABLED=true` in `.env` turns on an on-disk cache for chat-completion calls, sitting transparently inside `_call_api_with_fallback` — none of its 3 call sites (`run_agent`'s iteration loop, `run_leader`'s loop, and the spec-writer's spec-validation call) had to change.

**What it solves:** every one of those call sites can end up re-sending an identical prompt it has already paid for and waited on — retrying a whole feature cycle from scratch after a failure re-sends an identical first turn (system prompt + task, no tool-call history yet), and repeated spec-validation calls over an unchanged spec + file tree are also identical. This is a different problem than the existing checkpoint-based resumability (`_save_message_state`/`_load_message_state`): that avoids redoing a *crashed* run; this avoids redoing a *fresh* run that happens to reconstruct a prompt already seen (in a previous run, or earlier in this one).

**Cache key:** sha256 of a canonical JSON serialization (`sort_keys=True`, compact separators) of `(resolved_model, messages, tools)`. Critically, `resolved_model` is the *provider-resolved* model — `provider.resolve_model(model)` — not the caller-facing canonical name passed into `_call_api_with_fallback`, because `LLM_MODEL_MAP` can resolve the same canonical model to a different real model per provider (e.g. `deepseek-v4-pro` → `gpt-4o` on `openai`). The key is computed *inside* the per-provider retry loop, using that provider's own resolved model and its own sanitized outgoing messages (see `_sanitize_messages_for_provider`) — so this is not a DeepSeek-only cache. It works correctly across every provider in `LLM_FALLBACK_CHAIN` and every model in `MODEL_BY_ROLE`/`LLM_MODEL_MAP`: a cache entry is only ever reused for the exact provider+model+messages+tools combination that produced it, and an entry recorded for a fallback provider (because the primary was down when it was written) is only replayed later if that same provider ends up serving the request again.

**Storage:** one JSON file per hash under `LLM_CACHE_DIR` (default `progress/.llm_cache/`, override in `.env`) — flat JSON-per-entry, matching this codebase's existing plain-JSON persistence style (`feature_list.json`, `progress/spec_N.md`'s sibling `.json`, `progress/_state_*.json` checkpoints) rather than introducing sqlite for this. Harness-internal, like the `_state_*.json` checkpoint files that already live under `progress/` — not a directory agents are ever instructed to read or write.

**Interaction with cost reporting — cache hits are never counted as spend:** `_track_usage()` (which feeds `_SESSION_COSTS`, what `/costs` reports as real spend) is called unconditionally by every call site on whatever `_call_api_with_fallback` returns, without knowing whether it was a cache hit. A cache-hit response is reconstructed with `usage=None`, so that call is a no-op (`_track_usage` already early-returns on `usage=None`) — no code at the 3 call sites had to change to make this safe. The real saved token counts are recorded separately, before the cache-hit response is returned, into a new `_CACHE_STATS` dict (mirrors `_SESSION_COSTS`'s per-role shape: `hits`, `prompt_tokens_saved`, `completion_tokens_saved`, `savings_usd`, priced with the cached entry's own model via `_price_for_model` — same per-model pricing `_track_usage` uses). `progress/session_costs.json` gains a `cache` key (`enabled`, `by_role`, `hits`, `estimated_savings_usd`) alongside the existing `by_role`/`totals`, and the `/costs` console panel prints a `Cache hits: N | Est. savings: USD X (not spent, excluded from cost above)` line whenever hits > 0 this session — `estimated_savings_usd` is informational only and is never subtracted from `estimated_usd` (real spend).

**Nondeterminism, made explicit rather than silently assumed away:** no call site sets a `temperature` or `seed`, so two real calls with byte-identical input aren't guaranteed to return the same output either — enabling the cache just pins whichever sample was drawn the first time instead of drawing a fresh one on every retry. No caller in this codebase depends on getting a *different* response from an identical prompt (the existing spec/impl report caching — see [Structured status files](#structured-status-files) — already treats a prior successful attempt as reusable without re-running the LLM at all, on the same assumption), so this is a deliberate, documented opt-in tradeoff — which is exactly why it defaults off instead of being always-on.

`/cache` (status: enabled/disabled, entries on disk, hits and savings this session) and `/cache clear` (deletes all on-disk entries) are available in the REPL, matching the existing `/costs`/`/budget`/`/verbosity` command style.

---

## Roadmap

Active development continues in the premium edition. See the [⭐ Premium modules](#-premium-modules) section to learn about upcoming capabilities or get access.


---

## Changelog

### v1.55.0
- **New scoped `run_repro_script` tool: the implementer can finally observe browser-only bugs — via the repro script, without general host/Playwright access.** Real incident (feature #77): the symptom was observable only in the browser ("the switch flips back to true after save"). The implementer's TOOLS are read_file/write_file/list_files/run_bash/append_file — and under the default `SANDBOX_MODE=docker`, `run_bash` executes on an internal Docker network with **no route to the host** and no browser installed (nor should one be installed there; biovet's successful in-sandbox `curl` to the backend only worked because that project ran `SANDBOX_MODE=local`). `run_playwright_tests`/`take_screenshot` run host-side but are e2e_tester-only. The decisive evidence — the browser's actual request/response on the failing action — was structurally unobservable by the agent that had to fix the bug. Fix connects the v1.48.0 repro-gate pipeline end to end: (1) **`tools.run_repro_script(feature_id, timeout?)`** executes `progress/repro_<feature_id>.py` (harness interpreter, `sys.executable`) or `.sh` (bash) ON THE HOST — the same mechanism `run_playwright_tests` already uses — with the path derived from the integer-validated `feature_id` alone, never caller-supplied, so it grants no general host execution. Returns `passed` (exit 0), `returncode`, bounded output, and a `tip` that ties the result to the protocols: baseline `passed=false` is expected while the bug exists; a baseline that passes before any change means the spec's premise is refuted → PREMISE CHECK EXIT. Timeout ceiling via new `REPRO_SCRIPT_TIMEOUT_S` (default `180`, caps agent-supplied values). Missing script → error with a hint pointing at `REPRO: NOT_FEASIBLE` and the LAYER ISOLATION fallback. (2) **Scoped exposure** in `spawn_implementer` (which gained an `e2e: bool` parameter, plumbed from the feature cycle): the schema is appended to the implementer's toolset only for `e2e: true` features whose description reads as a bug fix (`_is_bugfix_feature`), and the injected repro-protocol task block names the exact call (`run_repro_script(feature_id=N)`) when — and only when — the tool is actually in the set. (3) **Prompt updates**: the implementer's REPRO SCRIPT PROTOCOL now states that `run_repro_script` is HOW the repro runs when present (host, live app + browser reach) and that `run_bash` is only for repros needing neither; the spec_writer's BUG-FIX RULE gained a browser-only clause — the Playwright repro MUST capture and print the network trace of the failing action (`page.on("request"/"response")` filtered to the endpoint, or a `page.request` replay) and quote the observed trace in the spec's root-cause section, since that trace is usually the decisive evidence (in #77 it showed the value returning CORRECT but at a different array position — indistinguishable from a persistence bug by UI observation alone). 11 new tests (`TestRunReproScript` — failing baseline, passing confirmation, `.sh` support, missing-script hint, feature_id integer validation as the anti-injection boundary, schema+dispatch registration; `TestReproToolExposure` — exposed for e2e bug fixes, hidden for `e2e: false` and for regular features, task block naming the call only when exposed).

### v1.54.0
- **The convergence watchdog got teeth: escalating nudge text, plus an optional zero-write hard cut (`MAX_ITER_WITHOUT_WRITE`).** Real incident (feature #77, round 2, attempt 1): the mechanism existed and FIRED — `CONVERGENCE_STREAK_LIMIT=7` plus the 60%/85% budget checkpoints injected ~11 notices — and the agent kept reading until iteration 80 without writing anything. The watchdog wasn't missing; it was toothless: the same polite paragraph repeated identically on every firing is easy for a model to under-attend to. Two additions, both env-configurable. (1) **Text escalation** — from the second streak firing on (`_no_write_streak // CONVERGENCE_STREAK_LIMIT >= 2`), the nudge becomes imperative and concrete: numbered (`CONVERGENCE CHECKPOINT #N — ESCALATED`), states that a previous checkpoint was ignored, and orders "your NEXT tool call MUST be write_file — your best partial fix, or a reproduction script capturing what you have verified so far. Anything else counts as a protocol violation." When the hard cut is armed and the attempt hasn't written yet, it also announces the exact iteration at which the harness will abort. (2) **Zero-write hard cut** — new `MAX_ITER_WITHOUT_WRITE` (default `40`, generous; `0` disables): if an attempt reaches that many **total** iterations with zero writes (tracked by a dedicated `_any_write_this_attempt` flag — `_no_write_streak` resets on every write, so it can't answer "has this attempt written anything"), the loop breaks early with a distinct `ZERO_WRITE_ABORT` log event and returns `[ERROR: attempt aborted: N iterations with zero writes (MAX_ITER_WITHOUT_WRITE=M)]` instead of the generic max_iter message. Combined with the v1.53.0 investigation digest — which the abort path also writes, with the cutoff reason in its header (`_build_investigation_digest` now takes a `cutoff_note` instead of `max_iter`) — dying at 40 with findings handed to the retry is strictly better than dying at 80 just as empty: it leaves half the budget for an informed retry. Downstream consumers of the error string updated to treat the new message like a max_iter cutoff: `_classify_error` gained the `"zero writes"` keyword (→ `LOGICAL`, not `FATAL`), and `spawn_e2e_tester`'s .md-recovery fallback matches both prefixes; the e2e max_iter report synthesis reuses the same `cutoff_note` in its verdict line. 6 new tests (`TestConvergenceEscalationAndZeroWriteCut`): soft text on the first firing / escalated imperative on the second, the abort announcement present only before any write, abort at the exact threshold with the API called exactly N times, a single early write preventing the abort entirely, the digest written on the abort path with the abort reason, and `0` disabling the cut.

### v1.53.0
- **Implementer max_iter cutoffs now hand their investigation to the next attempt — the existing mechanism re-fed errors, not knowledge.** Real incident (feature #77, round 2): `run_agent`'s `tool_call_errors` (last 5 tool errors appended to the `[ERROR: max_iter]` return) worked as designed in round 1, re-feeding the `edit_file` failure to the retry. But round 2's attempt 1 hit max_iter with **zero** tool errors — 107 clean `read_file` + 68 clean `run_bash`, including the key finding that `pytest tests/test_branches.py` passed in full — so attempt 2 started completely blind and repeated essentially the same investigation (51 reads before writing anything). New in `run_agent`, implementer-only, mirroring the e2e_tester's v1.38.0 max_iter evidence synthesis (feature #74): live trackers collect (a) a deduplicated ordered list of `read_file` paths, (b) `run_bash` commands with a one-line outcome each (new `_bash_outcome_line()` — for pytest that's its closing `"29 passed in 1.2s"` line; errors and empty output handled), and (c) the last assistant reasoning text before the cutoff, which usually contains the active hypothesis. Live trackers rather than a walk of `messages` at cutoff time, deliberately: `_compact_messages` can discard early history, but the trackers survive compaction (they don't survive a crash+resume — accepted, rare corner, best-effort). On a max_iter exit with anything collected, `_build_investigation_digest()` (deterministic, no LLM call, same philosophy as `_build_deterministic_digest`) writes a bounded digest (≤30 files, ≤12 commands with verification-looking ones — pytest/curl/psql/npm — prioritized over greps when cutting, ≤600-char hypothesis tail, ≤4000 chars total) to `progress/_investigation_impl_<id>.md` (underscore prefix — harness-internal, same convention as the `_state_*.json` snapshots). `spawn_implementer` (which now passes `feature_id` to `run_agent` — it never did before, the trigger for this synthesis) injects the digest into the task under a **"PREVIOUS ATTEMPT'S INVESTIGATION — do not re-derive this"** header, instructing the attempt to spend its budget on what the previous one did NOT reach. The digest is overwritten by each subsequent max_iter cutoff and injected whenever present — including a later round's attempt 1, which is exactly where round 2 of #77 started blind. 6 new tests (`TestImplMaxIterInvestigationDigest`): dedup + outcome line + hypothesis all land in the digest, no digest on a clean verdict, no digest for other roles, `_bash_outcome_line` variants, and both spawn-side injection paths.

### v1.52.0
- **Sanctioned PREMISE CHECK EXIT for the implementer, and a refuted-premise check on the spec cache.** Second structural gap from the feature #77 incident: a generated spec confidently asserted that the backend didn't persist `is_active` — false — and attempt 1 ran `pytest tests/test_branches.py` with everything PASSING, direct evidence the premise was false. But the implementer's protocol had no sanctioned way to report that: the only available behavior was to keep searching the refuted location until iterations ran out. Worse, the poisoned cached spec (`progress/spec_77.md`) was reinjected verbatim into every retry and re-run — verified in the log: the round-2 implementer spawned 2 seconds after `run_feature_cycle`, and the spec_writer was never re-invoked, because the only anti-poisoning check on the cache was `_spec_references_stale_e2e_test_dir` (v1.39.0). Two changes. (1) **`agents/implementer.py` PROTOCOL step 4b — PREMISE CHECK EXIT** (explicitly NOT an implementer failure): when direct verification contradicts the diagnosis the spec asserts about the existing code (the repro script doesn't fail where the spec says it must, or the tests of the exact layer the spec blames all pass), the implementer must not burn remaining budget in the refuted location — instead it writes a `PREMISE_CHECK: FAILED` section in `impl_<id>.md` (the spec's exact refuted claim + the command run + relevant output), adds `"premise_check": "failed"` to `impl_<id>.json` (structured, so the harness detects it without grepping markdown; omitted entirely when not applicable — `AgentStatusSchema` gained the optional field, with a value validator, so `extra="forbid"` doesn't reject the whole file), and ends the attempt returning the report path as usual. `spawn_implementer`'s cache check now also refuses to reuse a report with `premise_check == "failed"` — such a report can legitimately carry `tests_passed: true` (the passing tests ARE the refutation evidence), which would otherwise qualify it for reuse as a completed implementation. (2) **`harness.py` `spawn_spec_writer` — second cache check, same spirit and same `.stale` quarantine mechanics as the stale-e2e-path check:** before reusing a cached spec, new `_refuted_premise_evidence()` looks for `progress/diagnosis_<id>.json` with `"cause": "wrong_premise"` (written by an external plugin — the premium `failure_diagnostician` — after a feature's definitive failure; absent/corrupt = no-op, base ships the consumer and waits for its feeder, the same pattern `CONVERGENCE_RULE` followed), or `impl_<id>.json` with `"premise_check": "failed"` (read raw, so it counts even if another field wouldn't validate), with a fallback grep for `PREMISE_CHECK: FAILED` in `impl_<id>.md` for reports written before the structured field existed. On a hit: quarantine to `<spec>.stale` (`os.replace` + `SPEC_PREMISE_REFUTED` warning log + console notice) and regenerate, injecting into the spec_writer's task the refutation as a constraint — the evidence (the diagnosis `explanation`, or the `PREMISE_CHECK` section of the impl report, bounded to 800 chars) plus "do NOT reassert that premise; verify the actual behavior before asserting where the defect is." The evidence is computed even when the stale-e2e check already fired, so the regeneration task carries it either way. 8 new tests (`TestRefutedPremiseSpecCache`): diagnosis `wrong_premise` → quarantine + regeneration with evidence in the task; `impl.json` `premise_check` → regenerates with the `.md`'s PREMISE_CHECK section as evidence; `.md`-only fallback; other diagnosis causes, absent files, and corrupt JSON all reuse the cache untouched; a PREMISE CHECK EXIT report with `tests_passed: true` is never reused as an impl; `_read_structured_status` accepts the new field. Verified with the full suite (372 passed) and ruff (zero new findings vs. the previous commit).

### v1.51.0
- **`MINIMAL_DELTA_RULE` promoted to `agents/shared_rules.py` and interpolated into the E2E Tester too; three refinements to the v1.50.0 destructive-rewrite guard.** (1) The MINIMAL-DELTA REWRITES hard rule moved out of `agents/implementer.py`'s HARD RULES into `agents/shared_rules.py` (same import-and-interpolate convention as `CONTRACT_VERIFICATION_RULE`/`CONVERGENCE_RULE` — the file's own header exists precisely because a rule pasted twice drifts), and is now interpolated into both `agents/implementer.py` and `agents/e2e_tester.py` — the only two roles that write source-extension files. The e2e_tester-specific reason it can't rely on the implementer copy: when it extends an existing test file by regenerating it from memory, the lost tests break nothing — a deleted test never fails, coverage just shrinks silently, and the `write_file` warning naming the removed `def test_*` symbols is the only signal anyone will ever get, so the "treat the warning as ground truth about your own write and restore before doing anything else" instruction has to reach it too. Real evidence: in biovet-harness the E2E_TESTER wrote through the edit aliases (which write via `write_file()`) on features 34, 35, 36, 39, 46 and 51. The rule text also gained that deleted-test framing explicitly. (2) Guard refinements in `tools.py`: **multi-stack shrink coverage** — `.go .rb .php .java .cs` added to `_SOURCE_FILE_EXTS` (the stacks the harness supports: go-gin, ruby-rails, php-laravel, java-spring, dotnet-core); the shrink check is language-agnostic so this is free, but `_top_level_symbols()` now explicitly returns nothing for those extensions (previously the `else` branch would have applied the JS regexes to them — garbage matches, and a wrong "removed symbol" warning is worse than none; symbol regexes remain Python/JS/TS-only). **Size floor for the shrink check** — new `DESTRUCTIVE_SHRINK_MIN_LINES` (default `40`, env-overridable): a 10-line file dropping to 6 is a 40% "shrink" that's usually a legitimate refactor, and noise trains agents to ignore warnings — exactly the pattern this mechanism combats; the removed-symbols check deliberately has no floor (a dropped symbol is meaningful at any size). **Honest recovery wording** — the warning no longer promises `git show HEAD:<path>` universally: a file first created during the same feature isn't committed until approval, so git can't recover it; "the content you read earlier this run" is now the unconditional primary path, with git offered conditionally ("if the file already existed in the last commit"). 6 new tests (floor exempts small files + env-configurable, symbol check floorless on a 16-line file, `.go` shrink flagged with no symbol diff, conditional-git wording, and `TestMinimalDeltaRuleShared` asserting the shared rule text appears verbatim in both agents' `SYSTEM_PROMPT`s). **Deliberately out of scope for this repo:** the cumulative pre-approval symbol-removal gate over the feature-scoped diff belongs in `plugins/sdlc_governance.py`, which is a ⭐ premium module (see [Premium modules](#-premium-modules)) and does not exist in this repository — `tools._top_level_symbols()` is importable from there so the premium gate can reuse it without duplicating the regexes.

### v1.50.0
- **`write_file` now detects destructive rewrites of existing source files and returns a non-blocking `warning` naming exactly what was removed.** Real incident (feature #77, attempt 2, round 2): the implementer issued a single `write_file` that regenerated `backend/app/api/v1/branches.py` (~750 lines) *from memory* — an invented import (`from app.api.deps import ...`, a module that doesn't exist), the entire `POST /tenant/branches` endpoint deleted, a security `model_validator` deleted, and `await session.commit()` deleted. The debug-statements gate (74bac4b) only inspects **added** lines; nothing in the pipeline inspected what a full-file rewrite **removes** — and the identical regression from round 1 had already reached `origin/main`. The post-failure quarantine saved the working tree this time only because the feature happened to fail: had the mocked tests passed, the rewrite was on its way into a PR. Fix in `tools.py`: before overwriting, `write_file` compares the new content against the current on-disk content whenever the path already exists and has a source extension (`.py .js .jsx .ts .tsx .mjs .cjs` — reports/specs in `.md`/`.json` are exempt, they get legitimately rewritten shorter all the time). It flags (a) a shrink beyond `DESTRUCTIVE_SHRINK_RATIO` (default `0.30`, env-overridable, `0` disables the shrink check) and (b) top-level symbols present in the old version but missing from the new — cheap regex heuristic (`_top_level_symbols()`), not a parser: `def`/`class` plus `@router.<method>('<path>')` route decorators for Python; `function`/`class`/`export const` for JS/TS. The write always proceeds (never blocks the pipeline); the tool result gains a `warning` field naming the dropped symbols verbatim (e.g. `def create_branch, def _validate_is_active_consistency, @router.post('/tenant/branches')`) with instructions to restore anything unintentional — from content read earlier in the run, or via `git diff`/`git show HEAD:<path>` through `run_bash` — so the agent sees it on its very next turn and can self-correct within budget. Best-effort by design: any error in the check (`_destructive_rewrite_warning()`) makes it a no-op, never a failed write. The `edit_file`-alias translation writes through `write_file()`, so a hallucinated "edit" that hands over full new content inherits the same warning. Companion prompt rule in `agents/implementer.py` HARD RULES — **MINIMAL-DELTA REWRITES**: to modify an existing file, `read_file` it first and rewrite with the minimal delta over that real content (every unchanged line byte-identical to what was read); never regenerate a file from memory; and a `write_file` `warning` naming removed symbols is ground truth — restore every unintended deletion before doing anything else. 8 new tests (`tests/test_harness_core.py::TestDestructiveRewriteWarning`): removed Python symbols named exactly (and kept symbols NOT reported), shrink threshold flagged + env-configurable, pure addition / new file / non-source extension all clean, JS/TS removed exports named, and the edit-alias full-content path inheriting the warning.

### v1.49.0
- **The bug-fix repro requirement (v1.48.0) is now a blocking gate with a `REPRO: NOT_FEASIBLE` escape valve, plus an unconditional CONFIRMED→HYPOTHESIS downgrade at implementer injection.** v1.48.0's enforcement was advisory — a bug-fix spec without a repro script was annotated and waved through, which still let the feature #77 failure shape (confident prose, no verification) reach the implementer at full confidence on the very first attempt. Three changes. (1) **Blocking gate, one regeneration max** (`spawn_spec_writer`): a freshly generated bug-fix spec with neither a repro script on disk nor an explicit `REPRO: NOT_FEASIBLE — <concrete reason>` declaration in its text (new `_bugfix_spec_missing_repro()`) is quarantined to `spec_<id>.md.norepro` — same quarantine mechanism as the v1.39.0 stale-e2e-path check — and regenerated exactly once, with a `⚠️ REPRO GATE` block appended to the task naming both ways out: write `progress/repro_<id>.py`/`.sh` that fails while the bug exists, OR declare NOT_FEASIBLE with an honest reason (explicitly warning against fake repros written only to pass the gate). If the second spec still has neither, the gate falls back to the v1.48.0 behavior — annotate `## ⚠ Missing reproduction script` and continue — so the pipeline is never left hanging: strict on the common path, best-effort at the edge. (2) **The NOT_FEASIBLE valve** is also documented in `agents/spec_writer.py`'s BUG-FIX RULE so a first-pass spec can already use it: it converts "silently missing" into "consciously absent, with the reason visible to the implementer", which is exactly what prevents both regeneration loops and gate-gaming repros. The fallback annotation correspondingly no longer fires for specs that declared NOT_FEASIBLE. (3) **Downgrade invariant, independent of the gate** (new `_downgrade_unbacked_confirmed()`, applied in `spawn_implementer` when injecting the spec): any `CONFIRMED` label in a spec with no repro script attached is rewritten to `HYPOTHESIS (auto-downgraded: labeled CONFIRMED but no executable repro script is attached)` before the implementer ever sees it — word-boundary matched (UNCONFIRMED untouched), injection-only (the spec file on disk is never modified), logged as `SPEC_CONFIRMED_DOWNGRADED`. This guards every path the gate can't: the fallback path, cached specs from before this change, and hand-edited specs — so an unverified premise never travels with the confidence of a verified one, which was the exact mechanism of the #77 incident (confident prose treated as a confirmed diagnosis by `CONVERGENCE_RULE`'s apply-directly clause). `TestBugfixReproEnforcement` reworked/extended to 15 tests: exactly-one-regeneration + quarantine + gate task content, gate satisfied by a retry that writes the repro, NOT_FEASIBLE accepted first-pass with no regeneration, downgrade unit coverage (word boundary, no-op identity, repro-attached preservation) and both implementer-injection paths (downgraded without repro + disk file untouched; preserved with repro).

### v1.48.0
- **Bug-fix features now require an executable reproduction script, and root-cause claims must be labeled CONFIRMED or HYPOTHESIS.** Real incident (feature #77, biovet-harness, 2026-07-14/15): a bug-fix spec described its reproduction only in prose ("toggle the sede, save, the switch flips back to true") and asserted a backend persistence bug with total confidence; the implementer — correctly obeying `CONVERGENCE_RULE`'s "apply an injected diagnosis directly, don't re-litigate it" — burned 2 rounds × 2 attempts × `max_iter` 80 in a layer that was working fine. A human found the real cause in minutes by writing an *executable* repro: curl+psql proved `is_active` DID persist, and a Playwright request/response intercept showed the value coming back correct but at a different array position — the response was reordered by `ORDER BY ... is_active DESC` and the frontend rendered by position. Nothing in the pipeline could distinguish the spec's confident hypothesis from a verified diagnosis; that's the gap closed here, in four parts. (1) `agents/spec_writer.py`: new **BUG-FIX RULE** — any feature whose title/description reads like a bug fix must ship an executable repro script at `progress/repro_<feature_id>.py` (or `.sh`) that fails while the bug exists and passes once fixed, using the most direct route to the symptom (curl/httpx + direct DB check; sync Playwright only for UI-only symptoms); and every root-cause claim must be labeled **CONFIRMED** (stating how it was reproduced) or **HYPOTHESIS** (inferred from code, not verified) — unlabeled claims are treated as HYPOTHESIS downstream. The repro script is an explicit carve-out from the "do NOT implement code" rule (it's a diagnostic instrument in `progress/`, not feature code). (2) `agents/implementer.py`: new **REPRO SCRIPT PROTOCOL** hard rule — when the task includes a repro script, running it is the FIRST action (baseline) and the LAST action before reporting (fix confirmation); if the baseline does NOT fail as the spec claims, stop hunting where the spec points and report a **PREMISE DISCREPANCY** at the top of the impl report instead. Companion **LAYER ISOLATION** rule for persistence/wrong-value bugs *without* a repro: isolate the faulty layer with the most direct signal (curl bypassing the frontend + direct DB query) before reading source code — a symptom seen through the UI implicates every layer at once; the same check at API+DB level implicates exactly one. (3) `agents/shared_rules.py`: `CONVERGENCE_RULE`'s "apply that fix directly / do not re-litigate" point now applies **only to diagnoses labeled CONFIRMED with an attached repro**; HYPOTHESIS (or unlabeled) diagnoses get 3-5 iterations of direct verification before the agent commits its budget to that location — in #77 the implementer obeyed the old rule correctly over a false premise, so the rule itself was the hole. (4) `harness.py` enforcement, non-blocking in the `_validate_spec` philosophy: new `_is_bugfix_feature()` (cheap keyword heuristic, EN+ES) and `_existing_repro_script()`; `spawn_spec_writer` injects the exact repro path into a bug-fix feature's task (the prompt rule alone shouldn't have to trigger off title matching) and, if the generated spec has no repro script on disk, appends a `## ⚠ Missing reproduction script` section telling the implementer to downgrade every claim to HYPOTHESIS and layer-isolate first; `spawn_implementer` surfaces an existing repro script in the task with the run-first/run-last protocol (the implementer's rule triggers off "your task includes a repro script", so the harness must actually say so). 11 new tests in `tests/test_harness_core.py::TestBugfixReproEnforcement` (heuristic positives/negatives incl. Spanish phrasing, repro discovery, both task injections, annotation present/absent for bugfix-with-repro and non-bugfix specs).

### v1.47.0
- **Edit-tool alias gaps closed: bare `edit` tool name, and `search`/`replace`(`replacement`) argument names.** Real incident (feature #77, 2026-07-14, from `progress/harness.log`): attempt 1 called a tool literally named `edit` (not `edit_file`) — not in `_EDIT_TOOL_ALIASES`, so it got a bare "Tool 'edit' not found" with no translation. Attempt 2 called `edit_file` correctly but passed `"search"` instead of `old_string`/`old_str`/`old_text` — the only names `_edit_alias()` recognized — so it hit the same generic error. Both attempts had already investigated correctly (50+ `read_file`, 30+ `run_bash` each) and exhausted `MAX_ITER_IMPL` without writing the fix, purely on tool-call naming, not on understanding the problem. Fixed both gaps in `tools.py`: `"edit"` added to `_EDIT_TOOL_ALIASES` (line ~676), and `_edit_alias()` now also accepts `args.get("search")` as an alias for `old_string` and `args.get("replace")`/`args.get("replacement")` as an alias for `new_string` (line ~802), same pattern as the existing `old_str`/`new_str` aliases. 3 new tests in `tests/test_harness_core.py::TestTools` covering the bare `edit` name and both `search`/`replace` and `search`/`replacement` argument pairs, driven through `execute_tool()` end-to-end (not just the note-string mock the pre-existing `TestIsWriteCall` tests use).

### v1.46.0
- **The cycle itself marks an approved feature `"done"` before governance snapshots the branch — no longer relying on the Leader's later `update_feature_status` tool call.** Real incident (feature #75, biovet-harness, 2026-07-14): `after_feature_approved` runs `sdlc_governance._govern_feature()` synchronously, which branches, commits every currently-changed file, pushes/opens the PR, then `_return_to_base()` checks the base branch back out — resetting the working tree, `feature_list.json` included. The Leader's `update_feature_status(feature_id, "done")` tool call only happens a full LLM turn *after* `run_feature_cycle` returns, so the branch (and eventual squash-merged PR) captured the stale `"in_progress"` status, and the late `"done"` flip sat uncommitted on the base branch until the *next* feature's own `_govern_feature`/`_return_to_base()` checkout silently discarded it — a fully shipped, merged feature permanently reading `"in_progress"` (or `"pending"`, post-recovery). Fix: `_run_feature_cycle_impl` now calls the existing `_set_feature_status(feature_id, "done")` helper (v1.40.0, same read/mutate/write as `tools.update_feature_status()` — no second copy) inside the approval path, after the `before_approval_finalized` gate passes but *before* `_fire("after_feature_approved", ...)`, so the governance snapshot already contains the final status. Ordered before `_clear_checkpoint` so a crash in between can't resurrect a shipped feature as stale (`recover_stale_features()` skips `"done"`). This also covers `/auto`, whose own post-cycle `_set_feature_status(fid, "done")` had the identical timing hole; both callers' later `"done"` writes are now harmless no-ops, and the Leader tool is unchanged — it still owns `"failed"` and every other status. 2 new tests (`tests/test_resumability.py::TestStatusDoneBeforeGovernance`): one records the on-disk status at the exact moment `after_feature_approved` fires (must already be `"done"`), one asserts `feature_list.json` shows `"done"` after an approved return with no tool call involved.

### v1.45.0
- **`_validate_spec` sends head + tail of the spec instead of a flat `[:3000]` head-only cutoff.** With the old limit, the tests/notes section of any non-trivial spec fell entirely outside what the validation call ever saw — `spec_74.md`'s wrong E2E test directory (see the v1.39.0 stale-cache-quarantine entry) lived exactly there. New `_truncate_head_tail(text, head_chars=6000, tail_chars=6000)`: returns text unchanged when it's already within the combined bound, otherwise keeps the first `head_chars` and last `tail_chars` joined by a `"[...middle truncated...]"` marker. The header (files to touch) and the tail (tests, notes) are the sections with the most detectable issues, so keeping both ends catches more than simply raising a single head-only cutoff would. This is one cheap flash-model call — 9K chars of extra input costs cents against a wasted feature cycle. 6 new tests: `TestTruncateHeadTail` (unit coverage of the helper — unchanged-when-short, exact-boundary, head+tail+marker, custom bounds) and a new `TestValidateSpecStackAware` case proving a 20K-char spec's header and tail both reach the review call's message.

### v1.44.0
- **Per-feature cost tracking, plus an optional `FEATURE_BUDGET_USD` cutoff.** `COST_BUDGET_USD` is a global session limit — one pathological feature (feature #74's 2 full 50-iteration E2E cycles) can consume the entire session's budget with no mechanism ever noticing at the per-feature level. `_track_usage` now also accumulates into a new `_FEATURE_COSTS` dict, keyed by `_CURRENT_FEATURE_ID.get()` (the same contextvar already set for the duration of `run_feature_cycle` for structured logging — see the v1.20.0-ish structured-logging entry) whenever it's not `None`; calls made outside a feature cycle (Leader coordination turns, `_validate_spec`) aren't attributed to any feature, same as they already weren't in the by-role breakdown. New `_feature_cost_usd(feature_id)` helper. `progress/session_costs.json` gains a `per_feature` key (keyed by feature_id as a string, JSON-object-key convention) alongside the existing `by_role`/`totals`/`cache`. `/costs` now also prints a `Cost by feature` table (ID, title, calls, tokens, cost — sorted highest-spend first, red-highlighted once a feature crosses `FEATURE_BUDGET_USD`) via new `_print_per_feature_costs()`, which no-ops if no feature has made a tracked call yet. Optional extension in the same change: new `FEATURE_BUDGET_USD` env var (`0` disables, same convention as `COST_BUDGET_USD`) — checked at the top of every retry attempt in `_run_feature_cycle_impl` (not just once per cycle, since the point is cutting a feature off mid-retry the moment it crosses the line, not after paying for one more full impl→review→E2E attempt); once a feature's accumulated cost meets or exceeds it, the cycle stops immediately with a `[FEATURE_BUDGET_EXCEEDED]` `final_verdict`, clears the checkpoint, and fires `after_feature_failed` — same pattern the existing session-level budget guard already uses. 8 new tests (`TestPerFeatureCostTracking`, `TestFeatureBudgetCutoff`).

### v1.43.0
- **`_build_deterministic_digest` now keeps bounded content per result, not just the call list.** The v1.19.0-ish deterministic-digest fix (see the compaction-resumability section) replaced an LLM-summarized compaction with a factual list of "tool calls already made" — but the list only ever recorded call *signatures*, discarding every result. The digest told the agent "don't repeat these calls," then removed the only thing that would let it act on that instruction, forcing exactly the repetition it prohibited — confirmed on feature 74's log, where every compaction was followed by re-reading the same 3 files. Now correlates each `"tool"` result message back to the assistant call that produced it (via `tool_call_id`, a new `call_by_id` map built while walking the block) and retains, deterministically and boundedly: (a) the most recent `read_file` excerpt per unique path (~300 chars, head-truncated; a re-read of the same path overwrites the earlier excerpt — only the latest content survives) under a new "Key file contents already seen" section; (b) the tail (~500 chars) of the last `run_playwright_tests` `"output"`, since the traceback that matters is usually at the end; (c) the first ~5 stdout lines of any grep-shaped `run_bash` call (`"grep"` in the command string). Still no LLM call and nothing paraphrased — just more of the real bytes survive compaction. The whole digest is capped at a new `_DIGEST_MAX_CHARS = 4000` regardless of how much bounded content accumulates, since a richer digest still has to fit the context window it exists to protect. 7 new tests in `tests/test_compaction_resumability.py::TestDigestRetainsBoundedContent`.

### v1.42.0
- **Reordered the feature cycle: impl → review → E2E (was impl → E2E → review), closing ARCHITECTURE_REVIEW §8.C.** E2E is by far the most expensive step in the cycle (force-recreate + cold compile + real browser), but it used to run *before* the cheap, purely-static review check — every ordinary reviewer rejection wasted a full Playwright cycle it never needed. `_run_feature_cycle_impl` now runs the Reviewer right after the Implementer; only once review approves does E2E run at all. New checkpoint step `CKPT_REVIEW_DONE` ("review_done") sits between `impl_done` and `e2e_done` in the resumability progression (`_save_checkpoint`'s `_CheckpointSchema.step` is a bare `str`, so no schema change needed) — resuming from `review_done` skips straight to E2E; resuming from `e2e_done` now means *both* review and E2E already passed this attempt (E2E moved to last), so it skips everything and goes straight to finalizing, unlike the old order where `e2e_done` meant "run the reviewer next." The `before_approval_finalized` gate (used by the premium Human-in-the-loop-gates plugin) moved from firing right after review to firing only after E2E also passes, so a governance plugin can never finalize a feature whose E2E never ran. An E2E failure still retries the Implementer, and — new behavior, deliberate — the fix gets re-reviewed before E2E runs again, since a change made in response to an E2E failure can itself introduce something review would have caught. Updated the `tests/test_resumability.py` cases that encoded the old order's checkpoint semantics (`e2e_done` used to mean "run the reviewer next"; now means "everything already passed") and added `tests/test_harness_core.py::TestImplReviewE2eOrder` (4 new tests, e2e=True throughout — the only way to observe step order) proving a review rejection never reaches `spawn_e2e_tester`, the gate fires only post-E2E, a gate veto happens after E2E already ran (so the Playwright cost isn't "saved" by a later veto), and an E2E failure re-pays review on retry. Also updated the REPL banner, `README.md`'s "How it works" diagram, and `CLAUDE.md`'s architecture summary to reflect the new order.

### v1.41.0
- **`spawn_e2e_tester` injects previous-attempt evidence into a retry's task — the E2E-side counterpart to `spawn_implementer`'s own `RETRY #{attempt}` block.** Real incident, feature #74: attempt 2's log showed the same 3 files re-read and the same test re-run 3 times before hitting `max_iter` again, because the retry task was byte-for-byte identical to attempt 1's — `CONVERGENCE_RULE` already tells the agent to apply an injected diagnosis directly instead of re-deriving it, but nothing on the E2E side was actually injecting one. New `_e2e_retry_evidence_block(feature_id)`, called on `attempt > 1` and — critically — *before* the existing stale-report cleanup (v1.31.0) deletes `progress/e2e_<id>.json`, since at that point whatever's on disk is guaranteed to be the previous attempt's real evidence (or the harness-synthesized one from the v1.38.0 max_iter fallback). Builds a bounded, deterministic `⚠️ PREVIOUS E2E ATTEMPT FAILED — evidence:` block from that report's `"reason"` field, plus a `WHAT THE IMPLEMENTER CHANGED IN RESPONSE:` block combining `files_touched` from the retry's own `impl_<id>.json` (structured, reliable) with a bounded tail (last 1200 chars) of `impl_<id>.md`'s prose (design decisions, written last per the implementer's own PROTOCOL) — closing with "Start by re-running the exact failing test — do not re-derive context the evidence above already gives you." Returns `""` when neither source has anything (first real attempt, or truly nothing on disk), so task-building is never blocked. 5 new tests in `tests/test_harness_core.py::TestE2eRetryEvidenceBlock`, including one asserting the evidence survives being read before the cleanup step that deletes its source file.

### v1.40.0
- **New `/auto` REPL command: `run_all_pending()`, a deterministic code-level driver, as an alternative to the Leader-LLM for the common case of "process every pending feature."** Motivation: the Leader-LLM's own two real incidents this session already forced code-level guards — the dependency gate (v1.37.0) and the write/tool confinement (v1.36.0) — leaving what's essentially deterministic orchestration that an LLM executes slower, more expensively, and under a hard `MAX_ITER_LEADER` ceiling; a pending batch larger than that budget forces a human re-prompt mid-run today, working against unattended/autonomous processing. `run_all_pending()` reads `feature_list.json`, orders every feature with the existing `_topological_sort()` (full-graph order, then filtered to the `"pending"` subset, so a pending feature is only scheduled after everything it transitively depends on regardless of those dependencies' own status), and calls the exact same `run_feature_cycle()` the Leader calls for each in turn — so it benefits for free from both of the guards above. Sets each feature to `"in_progress"` before spawning (same convention `recover_stale_features()` already relies on for crash recovery) and to `"done"`/`"failed"` after, via new `_set_feature_status()` (same shape as `tools.update_feature_status()`, called directly with no LLM/tool-dispatch involved). Writes `progress/current.md`/`progress/history.md` with a new fixed template (`_write_auto_current_md()`/`_append_auto_history_md()`) instead of Leader-composed prose, covering the same fields `agents/leader.py`'s own PROTOCOL instructs the Leader-LLM to write by hand. Stops on: an empty queue (`"empty"`), structural dependency errors caught by the existing `_validate_dependencies()` (`"dependency_errors"`, re-checked here since `/auto` can run mid-session after the file changes), or the session budget being exhausted — checked via `_BUDGET_EXCEEDED` *before* each feature so the about-to-run one is left `"pending"` rather than incorrectly marked `"failed"` (`"budget_exceeded"`). `/auto <id>` (optional `only_feature_id`) runs just one feature, which must currently be `"pending"`. Does not replace the Leader-LLM — natural-language requests still go through `run_leader()` via the REPL's default input path; whether `/auto` eventually covers 100% of real usage is a follow-up decision, not made here. See the new [Deterministic batch driver](#deterministic-batch-driver) section. 8 new tests in `tests/test_harness_core.py::TestRunAllPending`.

### v1.39.0
- **`spawn_spec_writer`'s cache-reuse path now detects a spec poisoned with another e2e_runner profile's test dir, and quarantines + regenerates instead of reusing it.** Real incident: `spec_74.md` sent E2E tests to `e2e/biovet.spec.ts` — the legacy Node/@playwright/test suite from features #27-55 — while this project's resolved e2e runner is Python/pytest-playwright (`tests/e2e/*.py`). The implementer wrote 4 correct tests there that the real `run_cmd` never executes; because the cached spec is injected in full on every implementer retry (and survives any manual reset to `"pending"`), the poisoned path outlived every attempt. A prompt-only rule can't catch this because the spec_writer agent is never invoked on a cache hit in the first place — the check has to live in the cache-reuse branch itself. New `stack_layout.all_e2e_runner_profiles()` exposes the *full* `stack_profiles.json` `"e2e_runner"` map (not just the single active profile `resolve_layout()` resolves) — same best-effort discipline (`{}` on any read/parse error), `@lru_cache`'d the same way. New `harness._spec_references_stale_e2e_test_dir()` checks a spec's text against every *other* profile's `test_dir`+`file_ext` combo (e.g. `e2e/*.spec.ts`) and returns a description of the conflicting reference if found. `spawn_spec_writer`'s reuse branch now calls this on every cache hit: a clean spec is reused exactly as before (zero behavior change); a poisoned one is renamed to `spec_<id>.md.stale` (best-effort — if the rename itself fails, generation proceeds anyway since the fresh write overwrites `spec_path` regardless) and falls through to a full regeneration. No `stack_profiles.json` (or any read error) makes this a no-op, never a false positive or a block. 3 new tests in `tests/test_harness_core.py::TestSpecWriterStaleE2eCache`.

### v1.38.0
- **`run_agent` synthesizes a real `progress/e2e_<id>.json`/`.md` from captured Playwright evidence when the e2e_tester hits max_iter with no report at all.** Real incident, feature #74: the e2e_tester hit `max_iter` twice in a row without writing any report — not even a partial `.md` (a harder case than feature #71's v1.35.0 fix, which recovers from an `.md` that at least exists). The actual cause (a `TimeoutError` waiting on `#prof-name` after a successful login+submit — a `redirect()` bug in `layout.tsx`) was sitting in the `run_playwright_tests` tool results the whole time and was simply discarded once `max_iter` hit, leaving both the next implementer retry and the failure-diagnostician with nothing but the generic `"[ERROR: max_iter 50 reached]"` message — a blind retry instead of an informed one. `run_agent` now takes an optional `feature_id` (only `spawn_e2e_tester` passes it) and, purely for `role == "e2e_tester"`, keeps a running `_last_playwright_evidence` variable updated after every `run_playwright_tests` call — preferring the `"output"` field (pytest/Playwright stdout+stderr incl. traceback), falling back to `"error"` for the rarer case where the tool never got that far (install failure, subprocess timeout) — captured *after* the existing `_redact()` step, same as everything else that reaches `messages`/reports. If the loop exhausts `max_iter` and `progress/e2e_<id>.json` doesn't already exist, it's synthesized (`status: "failed"`, `tests_passed: false`, `reason`: the last ~1500 chars of that evidence, or a placeholder if `run_playwright_tests` was never called this attempt) alongside a minimal `.md` explicitly marked `"synthesized by harness after max_iter"`. Existing reports are never overwritten — this only fires when nothing else was written. Complements, rather than replaces, the v1.35.0 `.md`-recovery fallback in `spawn_e2e_tester` (which still exists as a secondary safety net for the near-impossible case where this synthesis itself fails to write): since this new synthesis runs first and unconditionally covers the "no report at all" case, in practice `spawn_e2e_tester`'s own fallback now rarely finds a missing `.json` to react to. 5 new tests in `tests/test_harness_core.py::TestE2eMaxIterReportSynthesis`, driving `run_agent`'s real loop (not a stub) through repeated `run_playwright_tests` calls to `max_iter`.

### v1.37.0
- **Code-level dependency gate in `_run_feature_cycle_impl`, right after the budget guard.** Real incident: the Leader started feature #72 via `run_feature_cycle` while feature #71 (a hard `depends_on`) had status `"failed"`, not `"done"`. The only protection was a prose instruction in the Leader's injected context ("Do not start a feature until all its depends_on features are done") — nothing in `run_feature_cycle()` itself enforced it, so a weaker model (or one under pressure after repeated failures) could and did ignore it. `_run_feature_cycle_impl` now reads `feature_list.json` (via the existing `_read_feature_list_raw()` helper — reused instead of a one-off `open`/`json.load`/try-except, since it already returns `[]` best-effort on any read/parse error) right after the budget guard and before the checkpoint-resumability block, and checks every one of the target feature's `depends_on` IDs against the other features' current `status`; any that aren't `"done"` short-circuits the whole cycle with a `[DEPENDENCY_ERROR]` `final_verdict` naming the unmet IDs and their actual statuses, logged at `error` level, before any sub-agent (spec/impl/e2e/review) is spawned. A missing/malformed `feature_list.json`, or a `feature_id` not found in it, is a no-op (not a block) — this is a safety net on top of the Leader's own judgment, not a replacement for `_validate_dependencies()`'s startup-time structural checks (self-dependency, missing IDs, cycles). 5 new tests in `tests/test_harness_core.py::TestDependencyGate`.

### v1.36.0
- **Leader confined to `progress/` at the tool-dispatch level; `run_bash` removed from its TOOLS.** `agents/leader.py`'s own system prompt says "You NEVER write code in src/ or tests/" and "Do not edit anything in src/ or tests/", but that was prose, not enforcement — `write_file`/`append_file` only check the tool-call path against the global `SAFE_WRITE_DIRS` (`src/`, `tests/`, `data/`, `progress/`, `docs/`, `frontend/`), which every role shares because implementer/e2e_tester/reviewer legitimately need `backend`/`frontend`/`tests` access. Real incident: the Leader rewrote `backend/app/api/v1/professionals.py` and `backend/tests/test_professionals.py` end-to-end while chasing a repeated E2E failure, introducing a real regression (called a password-hashing function that doesn't exist) — nothing in code stopped it, and it also used `run_bash` (`docker ps`, starting `uvicorn` manually, raw `asyncpg` against Postgres) even though its documented PROTOCOL only ever calls `write_file`/`append_file` on `progress/current.md`/`progress/history.md` plus `update_feature_status`/`read_feature_list`/`run_feature_cycle`. Fixed in two parts: (1) `execute_tool()` (`tools.py`) now takes a `role` parameter; when `role == "leader"` and the tool is `write_file`/`append_file`, the path must resolve inside `PROGRESS_DIR` or the call is rejected before reaching the real tool function — both call sites in `harness.py` (`run_agent`'s generic loop, and `run_leader`'s own bespoke loop, which doesn't call `run_agent` — see the v1.21.0 CONVERGENCE_RULE entry for that structural split) now pass `role` through. The path check reuses the exact same absolute-path/`"/workspace/"`-prefix normalization `_is_safe_path` already applies (extracted into `_normalize_agent_path()`), rather than a narrower one-off check, so a Leader write given in one of those forms isn't incorrectly rejected just because this check is scoped to `PROGRESS_DIR` alone instead of all of `SAFE_WRITE_DIRS`. (2) `run_bash` removed from `agents/leader.py`'s `TOOLS` entirely — no step in its PROTOCOL needs it. 7 new tests (`TestLeaderToolsSurface`, plus `TestTools` cases covering the progress/-confinement, the absolute-path normalization, and that non-Leader roles stay unrestricted).

### v1.35.0
- **`spawn_e2e_tester`: recover the real diagnosis from a fresh `.md` report when a max_iter timeout skips the `.json`.** Companion fix to the v1.34.0 same-turn-write rule, for the case that rule can't fully close: the agent had already written `progress/e2e_<id>.md` with the correct diagnosis before running out of iterations, but never reached the `.json` write. Real incident, feature #71: the `.md` correctly named a real backend 500 in `list_professionals` (found via the `page.request` replay from v1.33.0), but the harness discarded it in favor of `run_agent`'s generic `"[ERROR: max_iter 50 reached]\nRecent tool-call errors: read_file(...) -> not found"` — an unrelated tool error — as the only signal passed to the next implementer attempt and the failure-diagnostician. `spawn_e2e_tester` now checks, right after `run_agent` returns: if `result` starts with `"[ERROR: max_iter"`, no `.json` exists, but a `.md` does (guaranteed to belong to *this* attempt, not a stale one, by the v1.31.0 cleanup earlier in the same function), extract the report's last `"Verdict:"` line onward and use it as the effective `result` instead of the generic message. Fixed one bug in the proposed pattern before shipping it: the initial version searched for a `"## Verdict"` markdown heading, but `agents/e2e_tester.py`'s PROTOCOL (step 8, and `agents/reviewer.py`'s analogous rule) only ever instructs a plain `"- Verdict: ..."` bullet, never a heading — that pattern would never have matched a real report, silently never firing. Searches for `"Verdict:"` instead. 5 new tests in `tests/test_harness_core.py::TestSpawnE2eTesterMaxIterRecovery`, including one that would fail against the original `"## Verdict"` pattern.

### v1.34.0
- **E2E_TESTER: write progress/e2e_<id>.md and .json in the same turn — no longer two separate PROTOCOL steps.** Real incident, feature #71: the agent correctly diagnosed a real backend 500 (via the `page.request` replay from the v1.33.0 rule), wrote its findings to the `.md` report at the old step 8, then ran out of iterations before reaching the old step 9 — so `progress/e2e_<feature_id>.json` was never written. Since the harness only reads the `.json` (`_e2e_verdict()` prefers it over the prose report/returned string — see the v1.20.0/v1.29.0 entries) to propagate the real failure reason into the next retry and the diagnostician, that correct diagnosis was silently lost; the next attempt started from nothing. Merged the old steps 8 and 9 in `agents/e2e_tester.py`'s PROTOCOL into a single step 8 with an explicit mandatory instruction to write both files in the same turn, plus a fallback rule for the case that motivated this: if close to running out of iterations, writing only the `.json` with an accurate `reason` is strictly better than writing only the `.md`, since the `.json` is what the harness actually reads. The old step 10 (return the verdict string) is renumbered to step 9; no other PROTOCOL step references the old numbering.

### v1.33.0
- **E2E_TESTER: new AMBIGUOUS BROWSER NETWORK ERROR PROTOCOL hard rule — replay via `page.request` instead of theorizing about CORS.** Real incident, feature #72: after the `list_professionals()` UUID-cast bug (see the v1.32.0 entry) shipped a real backend 500, the E2E agent's browser only ever saw "Failed to fetch" plus a CORS-policy console error — a misleading symptom that shows up whenever a connection drops mid-response, not just on an actual cross-origin problem (this codebase's CORS is wide open, `allow_origins=["*"]`) — and burned the rest of its iterations theorizing about CORS/timing/race conditions instead of finding the real cause. `run_bash` can't inspect backend logs (sandboxed, no route to the host), but `page.request.get/post(...)` (Playwright's `APIRequestContext`, invoked inside a test executed through `run_playwright_tests`, which does have real network access) is a raw HTTP client that bypasses the renderer's `fetch()`/CORS layer entirely — replaying the same request this way surfaces the real status code and body regardless of what the page itself observed. Added as a new mandatory rule in `agents/e2e_tester.py`'s HARD RULES, right after WHICH TOOLS CAN REACH THE LIVE APP (the existing rule this one builds on for *which* tool can reach the live app at all): on any generic browser-level network failure (`"Failed to fetch"`, a CORS console error, `net::ERR_FAILED`/`net::ERR_ABORTED`), replay the exact request via `page.request` before forming any network/CORS hypothesis — only fall back to that hypothesis if the replay itself fails at the connection level in a way that isn't a normal HTTP error response.

### v1.32.0
- **IMPLEMENTER: raw-SQL rows must be explicitly cast before feeding a typed Pydantic field, even with a fully correct explicit column list.** The existing RAW SQL COLUMN EXISTENCE + RETURNING SAFETY rule only covered column *order* (`RETURNING *`/`SELECT *` + positional indexing silently breaking when physical column order diverges from the SET/INSERT list). Real incident, feature #72: `backend/app/api/v1/professionals.py`'s `list_professionals()` used a fully explicit, correctly ordered `SELECT p.id, p.user_id, ...` and still 500'd on every request once ≥1 professional existed, because `row[0]`/`row[1]` (Postgres `UUID` columns) were passed uncast into a `str`-typed Pydantic field — a type bug independent of column order. Added a new paragraph to the same rule in `agents/implementer.py`: whenever a raw SQL SELECT result feeds a Pydantic field typed `str`/`date`/`Decimal` mapped to a `UUID`/`TIMESTAMP`/`NUMERIC` column, cast it explicitly (`str(row[0])`, `row[1].isoformat()`, etc.) before constructing the model — the DB driver returns native UUID/datetime/Decimal objects, Pydantic does not silently coerce them, and FastAPI turns the resulting `ValidationError` into an opaque 500 with no detail in the response body. The crash never surfaced in backend unit tests because those mocked the DB session and never returned a real driver-native UUID — only E2E testing against the real database caught it.

### v1.31.0
- **E2E_TESTER: delete a stale `progress/e2e_<id>.md`/`.json` before spawning, unless a same-attempt crash-resume is pending.** `spawn_e2e_tester`'s report path carries no attempt number — always `progress/e2e_<id>.md`/`.json`, regardless of `attempt`. Real incident (feature #71): an attempt cut short by `MAX_ITER_AGENT` before writing its own report left a prior attempt's file on disk, written hours earlier by a different spawn; `_e2e_verdict()` prefers that structured JSON over the `"[ERROR: max_iter ... reached]"` string `run_agent` actually returned, so the stale file wasn't just misleading in a summary — it could silently flip the real pass/fail verdict (e.g. a prior attempt's `"status": "passed"` reused for an attempt that was never actually verified, since a passing e2e attempt only re-runs on a later attempt number after a *Reviewer* rejection, not an e2e one). `spawn_e2e_tester` now removes both files at spawn time — but only when `_load_message_state(checkpoint_key)` for this exact `attempt` is `None`. That check distinguishes a genuinely fresh spawn (which can only see a report from *some other* attempt — safe to delete) from an in-flight resume after a harness-process crash mid-attempt (`run_agent` clears its own message-state checkpoint on every clean return, verdict or max_iter, so non-`None` state means this exact attempt is still in progress) — in the resume case the model's resumed conversation may reference a partial report it already wrote this attempt, so it's left alone. Companion fix to the analogous problem already handled on the log-excerpt side of the premium `failure_diagnostician.py` module (scoping the excerpt to the last spawn) — this closes the same gap for the report file itself, in the base harness. 3 new tests in `tests/test_harness_core.py::TestSpawnE2eTesterStaleReportCleanup`.

### v1.30.0
- **Spec Writer: raw SQL with type casts or dynamic construction must get a real-database test, not just a mocked one.** A production bug (`:param::jsonb` breaking SQLAlchemy's bind-parameter parser) shipped past 43 tests that all ran against a mocked DB session, and was only caught by testing the real endpoint against Postgres — a mock never executes real SQL, so it can't catch an engine-level syntax error. New mandatory rule in `agents/spec_writer.py`'s HARD RULES: whenever a feature's spec includes a raw SQL statement (SQLAlchemy `text(...)` or the stack's equivalent) that does an INSERT/UPDATE with an explicit type cast (`::jsonb`, `::uuid`, etc.) or builds SQL dynamically (conditional `SET` clauses, variable column lists), the "Tests to write" section must include at least one case that runs against a real database (`TEST_DATABASE_URL` if the project already defines one, or the stack's equivalent) — with a note explaining why a mocked-session test alone can't catch this class of bug. Companion rule to the existing `agents/implementer.py` raw-SQL rules (RAW SQL INSERT COLUMN COMPLETENESS, RAW SQL COLUMN EXISTENCE + RETURNING SAFETY) — this one closes the gap at spec time instead of relying on the implementer alone to remember real-DB coverage.

### v1.29.0
- **Real schema validation and version-mismatch detection for the structured agent status JSON files (`progress/<stage>_<id>.json`).** Since [Structured sibling JSON status files](#structured-status-files) landed, `_read_structured_status()` only checked that a `"schema_version"` *key* was present — never that its *value* matched what the code actually knows how to interpret, and the literal `1` was hardcoded independently in all 4 agent prompts (`agents/implementer.py`, `agents/reviewer.py`, `agents/e2e_tester.py`, `agents/spec_writer.py`) with no single source of truth. The concrete risk: the day this shape's first real change ships (a field renamed, a new required field, `status`'s allowed values changed), a `progress/` directory generated by an older version of the harness would be silently read as if it were current — `.get("status")` not matching what the new code expects could produce a wrong reviewer/E2E verdict instead of a clear "I don't understand this file" signal. Fixed with the same pattern `FeatureSchema`/`FEATURE_SCHEMA_VERSION` already use for `feature_list.json`: a new `STATUS_SCHEMA_VERSION = 1` constant (in `tools.py`, not `harness.py` — it has to be importable from all 4 `agents/*.py` prompts, and `harness.py` imports `agents.*` at module load time, so a `harness.py`-side constant would be a circular import; `FEATURE_SCHEMA_VERSION` doesn't have this constraint since no agent prompt needs it) is now interpolated into every prompt's JSON template instead of a bare `1`, and a new `AgentStatusSchema` (pydantic, `extra="forbid"`, same style as `FeatureSchema`) validates the shape in `harness.py`. A `schema_version` mismatch is checked *before* full validation and logged as its own distinct event, `STATUS_SCHEMA_VERSION_MISMATCH` (warning level) — not folded into the same silent `None` a missing/corrupt file already produced — so a future version bump has somewhere to land with a clear signal in `progress/harness.log`, instead of a subtly wrong read with no trace. `status`'s value is checked against `_AGENT_STATUS_VALUES`, the union of every value any of the 4 writers can legitimately produce today (`"ok"`, `"done"`, and `tools.STATUS_APPROVED`/`STATUS_REJECTED`/`STATUS_PASSED`/`STATUS_FAILED`) rather than per-role — `_read_structured_status()` is deliberately role-agnostic (it's handed a bare path, not told which agent wrote it) and its 3 call sites don't pass a role today, so per-role vocabulary would need new plumbing for marginal benefit over catching what actually matters: a hallucinated status belonging to no role at all (e.g. `"complete"` instead of `"done"`). The never-raise contract is unchanged and this is purely additive: a mismatch or validation failure still returns `None`, and every caller (`_reviewer_verdict`, `_e2e_verdict`, `spawn_implementer`'s cache check) already treats `None` as "fall back to the prose heuristic" — nothing behaves differently for the one schema version that exists today. No v2 shape or migration code was written — there's nothing to migrate yet; this ships the mechanism so a future bump has one. Deliberately out of scope: `progress/session_costs.json`, the `_state_*.json` message-state snapshots, and `.llm_cache/*.json` entries (see [LLM response cache](#llm-response-cache)) are harness-internal, not agent-authored and not part of the human-facing audit trail the impl/review/e2e/spec reports are, so the same silent-drift risk is lower-consequence there — left unversioned for now, revisit if one of them grows a second shape in the wild. 13 new tests extending `tests/test_harness_core.py::TestReadStructuredStatus` (current-version happy path, future/unknown version detected as mismatch with distinct logging, wrong field type / missing required field / unknown extra field / unknown status value all fail validation gracefully, every real role's status value accepted, optional fields may be omitted) — the pre-existing absent-file/corrupt-JSON/missing-`schema_version` fallback tests are unchanged, confirming this stays non-breaking for `progress/` directories from before this change.

### v1.28.0
- **Opt-in on-disk cache for LLM API responses (`LLM_CACHE_ENABLED`, default `false`).** Every chat-completion call in the codebase funnels through one function, `_call_api_with_fallback`, called from exactly 3 sites (`run_agent`'s iteration loop, `run_leader`'s loop, and the spec-writer's spec-validation call) — and each of those sites can re-send a prompt it has already paid for and waited on: retrying a whole feature cycle from scratch after a failure re-sends an identical first turn (system prompt + task, no tool-call history yet), and repeated spec-validation calls over an unchanged spec + file tree are also identical. Distinct problem from the existing checkpoint-based resumability (`_save_message_state`/`_load_message_state`, see [Resuming after a crash](#resuming-after-a-crash)): that avoids redoing a *crashed* run; this avoids redoing a *fresh* run that reconstructs a prompt already seen. Implemented entirely inside `_call_api_with_fallback` — none of the 3 call sites changed. Cache key: sha256 of a canonical JSON serialization of `(resolved_model, messages, tools)`, computed per-provider inside the existing retry loop using `provider.resolve_model(model)` (the provider-resolved model, not the caller-facing canonical name) and that provider's own sanitized outgoing messages — so this is explicitly not DeepSeek-only: it's correct across every provider in `LLM_FALLBACK_CHAIN` and every `LLM_MODEL_MAP`/`MODEL_BY_ROLE` model combination, and a cache entry recorded for a fallback provider only replays if that same provider ends up serving the request again. Storage: one JSON file per hash under `LLM_CACHE_DIR` (default `progress/.llm_cache/`), matching the codebase's existing plain-JSON persistence style rather than adding sqlite; harness-internal, same convention as the `_state_*.json` checkpoint files already living under `progress/`. **Cost reporting stays honest:** a cache-hit response is reconstructed with `usage=None`, so the 3 call sites' unconditional `_track_usage(role, api_response.usage, ...)` call is a no-op on a hit (relying on `_track_usage`'s existing `usage is None` early return) — real savings are recorded separately into a new `_CACHE_STATS` dict (mirrors `_SESSION_COSTS`'s shape: `hits`, `prompt_tokens_saved`, `completion_tokens_saved`, `savings_usd`) *before* the no-usage response is returned, so `/costs` and `progress/session_costs.json` (`cache.hits`, `cache.estimated_savings_usd`) can report cache savings without ever letting them look like real spend. New `/cache` (status) and `/cache clear` REPL commands, matching the existing `/costs`/`/budget`/`/verbosity` pattern. See the new [LLM response cache](#llm-response-cache) section. Nondeterminism is documented rather than silently assumed away: no call site sets `temperature`/`seed`, so caching pins one drawn sample instead of a fresh one per retry — a tradeoff made explicit precisely because it defaults off. 32 new tests in `tests/test_llm_cache.py` (key composition/canonicalization, miss-then-hit with no second provider call, `_SESSION_COSTS` isolation from hits, tool-call round-tripping through the cache, and persistence across a fresh module import).

### v1.27.0
- **E2E_TESTER: `error-context.md` tip pointed to a file that doesn't exist for Python/pytest-playwright projects, burning a real project's full 50-iteration budget.** Real incident on a downstream onboarding-wizard feature: two E2E test functions in the same file each registered a new tenant with unique `clinic_name`/`admin_email` (uuid4-derived) but reused the same hardcoded NIT (`"900123456"`) — the backend correctly rejected the second tenant's submit as a duplicate NIT and rendered a visible error banner, but the test's `wait_for_url` just timed out with no explanation. The agent had no textual path to the real cause: `_run_playwright_tests_python()` in `tools.py` returned a `"tip"` telling it to read `error-context.md` in the matching `test-results/<test-name>/` subfolder — an artifact only `@playwright/test` (Node) generates; `pytest-playwright` never writes one. `agents/e2e_tester.py`'s own protocol (step 7 and the IMMEDIATE-FIX PROTOCOL) repeated the same unconditional claim. With no such file, no vision (the prompt itself already notes this harness's LLM has no image input, so a failure screenshot is useless as evidence anyway), and no hint to check its own test's uniqueness assumptions, the agent spent all 50 iterations on selector/regex trial-and-error before improvising an error-toast locator far too late. Fixed both sources: `_run_playwright_tests_python`'s `"tip"` now says the "output" field (last ~3000 chars of pytest stdout/stderr, already including the traceback) is the authoritative source, and that a bare timeout with no explanation likely means the page rendered a visible error the test never checked for (`_run_playwright_tests_node`'s tip, which is correct for Node, is unchanged). `agents/e2e_tester.py` step 7 and the IMMEDIATE-FIX PROTOCOL now tell the agent to trust the runner's own `"tip"` field for whether `error-context.md` applies, instead of asserting it unconditionally. Also added a new UNIQUE TEST DATA mandatory rule (next to the existing TEST ISOLATION rule) requiring any backend-enforced-unique field (tax ID, email, username, slug, phone, etc.) to be derived from the same per-test random suffix as other identity fields, never a hardcoded literal — directly closing the root cause of the incident, not just the missing-diagnostic symptom.

### v1.26.0
- **Convergence-over-exploration: a dynamic no-write streak detector in `run_agent`, plus a shared prompt rule across all 5 agents.** A recurring failure mode reported from live runs: an agent (implementer especially) keeps reading/grepping/re-verifying well past the point it already knows what to change, burning its iteration budget on exploration instead of making the edit — most visibly right after a diagnosis was already handed to it in the task description, or right after a compaction event, where it re-reads files it already saw rather than trusting the compacted summary. The first draft of a fix was a purely static prompt rule (a fixed "after 6-8 reads, stop" instruction). Rejected as the sole fix: `run_agent`'s own `BUDGET CHECKPOINT` mechanism (see the v1.21.0 entry below) exists precisely because this codebase already learned that a static system-prompt-only nudge is easy for a model to under-attend to as a conversation grows — asking the model to self-count its own turns silently reintroduces that exact weakness, and a bare "6-8" is also an unparametrized magic number disconnected from each role's actual `MAX_ITER_*` budget. Instead, split the fix in two, mirroring how the codebase already separates harness-enforced counting from prompt-conveyed judgment: (1) `run_agent` now tracks a real streak — consecutive iterations with tool calls but no `write_file`/`append_file`/`update_feature_status` (new `_is_write_call()`, which also recognizes a hallucinated `edit_file`-style call that `_edit_alias` transparently translated into a real write, via that translation's exact success marker, so a *failed* alias attempt doesn't falsely count) — and injects a live "CONVERGENCE CHECKPOINT" message every `CONVERGENCE_STREAK_LIMIT` iterations of the streak (default `7`, `.env`-overridable, `0` disables it), reusing the same injection pattern as the existing budget checkpoint; the streak resets to `0` the moment a real write happens. (2) The judgment side — don't re-derive a diagnosis the task already handed you, don't re-read a file already read this run even across compaction (except the exact line being edited), a repeated test run without an intervening edit is stalling not verifying, and trust the harness's own nudge rather than arguing with it — became `CONVERGENCE_RULE` in `agents/shared_rules.py`, imported into all 5 agent prompts (`leader`, `spec_writer`, `implementer`, `reviewer`, `e2e_tester`) the same way `CONTRACT_VERIFICATION_RULE` already is, since none of the five agents are immune to this failure mode. Base-repo change (system prompts + `harness.py`), not a premium plugin — it doesn't touch `_HOOKS`/`plugins/` at all. Note: the Leader's own loop (`run_leader`) is a separate, bespoke loop that doesn't call `run_agent`, so it gets `CONVERGENCE_RULE`'s prompt text but not the dynamic streak detector — a pre-existing structural gap between the two loops, out of scope here. 8 new tests in `tests/test_harness_core.py` (`TestIsWriteCall`, `TestConvergenceStreakDetector`), including a regression proving the checkpoint fires at the exact iteration the streak crosses the limit (not before) and that alternating reads/writes never lets the streak accumulate.

### v1.25.0
- **New lifecycle hook: `after_reviewer_rejected`.** The existing `after_feature_failed` only fires once a feature exhausts all `MAX_RETRIES_REVIEW` attempts — a plugin that wants to observe *every* Reviewer rejection (e.g. to log per-attempt failure diagnostics, or feed a root-cause-analysis pipeline as attempts happen rather than only at the end) had no event to hook. Added `after_reviewer_rejected` to `_HOOKS` in `harness.py`, fired via `_fire()` (observational only, not a gate) right after the existing `CYCLE_RETRY` log line in `run_feature_cycle`'s retry loop, with kwargs `feature_id`, `description`, `attempt`, `max_attempts`, `rejection_reason`. Scoped deliberately narrow: it fires only for a genuine Reviewer rejection, not a `before_approval_finalized` plugin veto or an E2E failure, so a plugin author can tell the three failure sources apart instead of conflating them. Purely additive — no existing hook's behavior changed. Documented in `plugins/example_plugin.py`'s "AVAILABLE EVENTS" list with a commented-out example. 2 new tests in `tests/test_harness_core.py::TestAfterReviewerRejectedHook`, covering both the multi-rejection case (correct `attempt`/`max_attempts`/`rejection_reason` per call) and the case where a later attempt is approved (hook fires only for the rejected attempt, not the approved one).

### v1.24.0
- **`CONTRACT_VERIFICATION_RULE` shared across all 5 agent prompts, via new `agents/shared_rules.py`.** Each agent's `SYSTEM_PROMPT` previously had to carry its own copy of any cross-cutting rule; a rule pasted 5 times drifts the moment one copy is edited and the others aren't. `agents/shared_rules.py` now holds `CONTRACT_VERIFICATION_RULE` — a "read the real interface before relying on it, don't invent a value the interface doesn't accept as input" rule aimed at the class of bug where an agent hallucinates a plausible-looking field/param/flag — and `leader.py`/`spec_writer.py`/`implementer.py`/`reviewer.py`/`e2e_tester.py` all `import` and interpolate it rather than defining their own text. A future edit to the rule now reaches every agent in one change.
- **10 hardcoded values found during that same drift audit centralized into `tools.py`/`stack_layout.py` constants.** Same rationale as above, applied project-wide: `FEATURE_LIST_PATH`/`PROGRESS_DIR` replace ~20 repeated `"feature_list.json"`/`"progress/"` literals across `harness.py`; `ROLES = ("leader", "spec_writer", "implementer", "reviewer", "e2e_tester")` is now the single source of truth for the 4 role-keyed dicts (`MODEL_BY_ROLE`, `_SESSION_COSTS`, `_AGENT_STYLES`, plus `spawn_*` call sites), with startup `assert`s so a typo in one of those dicts fails loudly instead of silently misattributing cost/style tracking via a `.get(role, "leader")` fallback; `STATUS_APPROVED`/`STATUS_REJECTED`/`STATUS_PASSED`/`STATUS_FAILED` and `VERDICT_APPROVED`/`VERDICT_REJECTED`/`VERDICT_E2E_PASSED`/`VERDICT_E2E_FAILED` tie the reviewer/e2e_tester prompts' literal output strings to the harness's own comparisons; `MAX_RETRIES_API`/`MAX_RETRIES_IMPL`/`MAX_ITER_LEADER` now respect `.env` overrides like their `MAX_ITER_*` siblings already did (see the updated [Configuration](#configuration) table); the E2E `base_url` is derived from a new `"port"` field in `stack_profiles.json` / `stack_layout.py`'s resolved layout instead of a hardcoded `http://localhost:8000`, so a project whose backend listens elsewhere doesn't get E2E silently pointed at the wrong URL; `E2E_SUBPROCESS_TIMEOUT_S`/`MUTATION_TEST_TIMEOUT_S` back both the `subprocess.run(..., timeout=...)` calls and their "N minutes" error messages so the number and the message can't desync; `SCREENSHOTS_DIR` replaces 6 occurrences of `"tests/screenshots"`; and 3 checkpoint-step strings (`CKPT_SPEC_DONE`/`CKPT_IMPL_DONE`/`CKPT_E2E_DONE`) replace literal comparisons in `harness.py`.

### v1.23.0
- **API key leak: agents could read `.env` and could see it via the local sandbox's inherited environment; tool results are now redacted before logging or reaching the LLM's own context.** A user reported the DeepSeek key showing up in logs. Root cause was two independent gaps: (1) `read_file` (`tools.py`) has no path confinement — unlike `write_file`/`append_file`, which respect `SAFE_WRITE_DIRS` — so `read_file(".env")` returned the raw file contents, and `list_files` surfaced `.env` in directory listings (only hidden *directories* were pruned during traversal, not hidden files in the current one); (2) `SANDBOX_MODE=local` (opt-in, and the automatic fallback when Docker isn't installed) ran agent shell commands via `subprocess.run()` with no explicit `env=`, so a command like `env`/`printenv` inherited and could print the harness's own `DEEPSEEK_API_KEY` from the host process. Either path fed straight into `_log(role, "TOOL_RESULT", result[:200])`, landing in `progress/harness.log` and the JSON stdout stream. Fixed in three layers: `read_file`/`list_files` now refuse/omit any `.env*`-pattern path (new `_is_secret_path()` in `tools.py`); `SANDBOX_MODE=local` now strips every `*_API_KEY` variable from the subprocess environment (`sandbox.py`'s new `_sanitized_host_env()` — Docker mode was already unaffected, it never passed host env into containers); and a new `_redact()` in `harness.py`, seeded once at startup with every configured `*_API_KEY` value, is applied to a tool's result immediately after execution — before it's logged *and* before it's appended to the LLM's own conversation history, since that history gets written into `progress/*.md` reports too — plus inside `_log()` itself as a backstop for any other message text. See the new [Secrets protection](#secrets-protection) section. 17 new tests across `tests/test_harness_core.py` (`TestRedact`, `TestToolResultRedactionInRunAgent`, `TestSandboxLocalEnvSanitization`, plus `.env`-blocking cases in `TestTools`).
- **Startup banner renamed to "Vora Engine".** `console.rule("Multi-Agent Harness", ...)` in `main()` was the only place that literal string appeared in any runtime output; nothing else (docs, package name) was touched.

### v1.22.0
- **`_validate_spec` false positives from a silently truncated file tree; also made stack-aware.** `_file_tree(path, max_files=60)` sorts alphabetically and truncates — a file whose path sorts past position 60 (e.g. `tests/test_migrations.py` in a large `tests/` dir) simply didn't appear in the list, and `_validate_spec` read that absence as "this file doesn't exist," appending a bogus warning to the spec (non-blocking, but noise the implementer has to mentally discard). `_file_tree` now appends a note when it truncates (`"N files total, showing first 60 alphabetically — this list is truncated, absence from it is not proof a file doesn't exist"`), and `_validate_spec`'s prompt explicitly tells the reviewing LLM not to flag a file as missing solely because it's absent from a list that says it's truncated. Separately, `_validate_spec` was hardcoding `_file_tree("src")`/`_file_tree("tests")` instead of the stack-resolved `CODE_TREE_DIRS` that every other `spawn_*` function already uses (`stack_layout.py`'s whole reason for existing) — silently useless for a project whose stack profile names its source dir something other than `src/`. Now iterates `CODE_TREE_DIRS` like the rest of the pipeline. 4 new tests in `tests/test_harness_core.py` (`TestFileTree`, `TestValidateSpecStackAware`).
- Investigated a second reported issue (an `agent_memory.py` plugin failing on a `{type, key, value}` → `{type, summary, feature_id, recorded_at}` schema change in `data/agent_memory.json`) — confirmed that plugin and data file don't exist anywhere in this repo (only `plugins/example_plugin.py` does); it's the premium **Agent memory across features** module, so the fix belongs in the premium fork's own working tree, not here.

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
