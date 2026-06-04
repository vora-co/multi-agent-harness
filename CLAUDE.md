# CLAUDE.md — Project Context

## What this is
A multi-agent harness built on DeepSeek API that automatically builds web applications feature by feature. Five agents: Leader → Spec Writer → Implementer → E2E Tester → Reviewer.

## Key commands
```bash
# Run the harness
python3 harness.py

# Install dependencies
bash init.sh

# Run unit tests (once the app has tests)
python3 -m pytest tests/ -v
```

## Harness architecture
```
harness.py          # Main engine — REPL + leader loop
agents/leader.py    # Coordinates features (important system prompt)
agents/spec_writer.py
agents/implementer.py
agents/reviewer.py
agents/e2e_tester.py
tools.py            # Tools available to agents
feature_list.json   # Feature state (pending/in_progress/done/failed)
progress/           # Reports per feature (spec_N.md, impl_N.md, review_N.md)
```

## How to use the harness
```
python3 harness.py
You → process all pending features     # process all in order
You → run only feature 2 and stop      # run a specific feature
/features   # view status
/costs      # token costs
```

## Key design decisions
- `SAFE_WRITE_DIRS` in `tools.py` controls where agents can write
- `e2e: false` on features skips Playwright — use for backend-only features
- Spec writer caches existing specs (`progress/spec_N.md`) — won't regenerate if exists
- Implementer caches impl if `progress/impl_N.md` shows tests passing
- Reviewer in lightweight mode for frontend: only checks files exist, doesn't run servers
- `datetime.fromisoformat()` in Python 3.9 doesn't accept `Z` or milliseconds — use `.toISOString().split('.')[0]` in JS

## To reset a failed feature
```python
python3 -c "
import json
with open('feature_list.json') as f: features = json.load(f)
for f in features:
    if f['id'] == N: f['status'] = 'pending'
with open('feature_list.json', 'w') as f: json.dump(features, f, indent=2)
"
```
