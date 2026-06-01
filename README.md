# Multi-Agent Harness

A multi-agent harness built on DeepSeek API that automatically builds web applications feature by feature — using five specialized AI agents: **Leader**, **Spec Writer**, **Implementer**, **Reviewer**, and **E2E Tester**.

You define what to build in `feature_list.json`. The harness does the rest.

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
- A [DeepSeek API key](https://platform.deepseek.com/)

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/your-username/multi-agent-harness
cd multi-agent-harness
```

### 2. Configure your API key

Create a `.env` file in the root:

```env
DEEPSEEK_API_KEY=your_api_key_here
```

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
| `continúa con las features pendientes` | Processes all pending features in order |
| `Ejecuta solo la feature 3 y detente` | Processes only feature #3 |
| `Procesa las features 2 y 3` | Processes a specific range |
| `/features` | Shows the status of all features |
| `/costos` | Shows token usage and estimated cost for this session |
| `/estado` | Shows the current state (progress/current.md) |
| `/salir` | Exits the harness |

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

Token usage is tracked per agent. Run `/costos` in the REPL or check `progress/session_costs.json` after a session.

Typical cost per feature: **~$0.05–0.15 USD** with DeepSeek v4-pro depending on complexity.

---

## License

MIT
