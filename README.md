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
    "created_at": "2025-01-01T00:00:00"
  },
  {
    "id": 2,
    "title": "REST API: user authentication",
    "description": "Create src/auth.py with JWT auth (python-jose). Add POST /api/v1/auth/register and POST /api/v1/auth/login endpoints to src/api.py. Hash passwords with bcrypt. Return JWT token with payload {user_id, role, exp: 24h}. Tests in tests/test_auth.py.",
    "status": "pending",
    "e2e": false,
    "created_at": "2025-01-01T00:00:00"
  }
]
```

### Feature fields

| Field | Type | Description |
|---|---|---|
| `id` | int | Sequential ID — features run in ascending order |
| `title` | string | Short name for the feature |
| `description` | string | Full spec: files to create, logic to implement, tests to write |
| `status` | string | `pending` \| `in_progress` \| `done` \| `failed` |
| `e2e` | bool | `true` only for features with browser UI to test with Playwright. Keep `false` for backend features |
| `created_at` | string | ISO timestamp |

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
Tú → continúa con las features pendientes
```

### REPL commands

| Command | What it does |
|---|---|
| `process all pending features` | Processes all pending features in order |
| `run only feature 3 and stop` | Processes only feature #3 |
| `process features 2 and 3` | Processes a specific range |
| `/features` | Shows the status of all features |
| `/costs` | Shows token usage and estimated cost for this session |
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
| `MODEL` | `deepseek-v4-pro` | DeepSeek model. Use `deepseek-v4-flash` for lower cost |
| `MAX_ITER_LEADER` | `30` | Max iterations for the Leader agent |
| `MAX_ITER_IMPL` | `50` | Max iterations for the Implementer |
| `MAX_ITER_REVIEWER` | `40` | Max iterations for the Reviewer |
| `MAX_RETRIES_REVIEW` | `2` | Times the impl→review cycle retries before marking failed |

---

## Safe write directories

Agents can only write to these directories (controlled in `tools.py`):

```python
SAFE_WRITE_DIRS = ("src/", "tests/", "progress/", "docs/", "frontend/", "data/")
```

Add more directories here if your project needs them.

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
- **Feature dependency graph** — declare that feature 5 depends on feature 3, so the harness can determine which features can run in parallel
- **Per-agent model selection** — use a cheaper model (e.g. flash) for the Spec Writer and a more capable one for the Implementer, reducing cost without sacrificing quality
- **Agent memory across features** — agents currently start fresh each feature; persisting learned conventions and decisions across features would reduce repeated mistakes

### Harness UX
- **Web dashboard** — replace the terminal REPL with a browser UI showing live agent progress, feature status, cost tracking, and logs
- **Cost budgets** — set a maximum spend per session or per feature; the harness stops and alerts when the budget is reached
- **Webhook notifications** — notify Slack, email, or any webhook when a feature completes or fails

### Reliability
- **Smarter retry logic** — currently retries with the full rejection reason; could extract specific failing tests and inject only the relevant context
- **Spec validation** — the Spec Writer could verify its own spec against existing code before handing off to the Implementer, catching contradictions earlier
- **Incremental context compaction** — the current compaction strategy is conservative; a more aggressive approach could reduce token usage on long sessions by 30–40%

---

## Changelog

### v1.1.0
- All code, comments, and agent prompts translated to English
- REPL commands now accept both English (`/quit`, `/costs`, `/status`) and Spanish aliases
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
