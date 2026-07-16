# CLAUDE.md — Project Context

## What this is
A multi-agent harness built on DeepSeek API that automatically builds web applications feature by feature. Five agents: Leader → Spec Writer → Implementer → Reviewer → E2E Tester. (Review runs before E2E — the cheap, purely-static check happens before the most expensive step in the cycle, so an ordinary reviewer rejection never wastes a full Playwright cycle.)

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
You → process all pending features     # process all in order (via the Leader-LLM)
You → run only feature 2 and stop      # run a specific feature (via the Leader-LLM)
/auto       # deterministic equivalent of "process all pending features" — no LLM orchestration
/auto 2     # deterministic equivalent of running just feature 2
/features   # view status
/costs      # token costs
```

## Key design decisions
- `SAFE_WRITE_DIRS` in `tools.py` controls where agents can write
- `e2e: false` on features skips Playwright — use for backend-only features
- Spec writer caches existing specs (`progress/spec_N.md`) — won't regenerate if exists
- Implementer caches impl if `progress/impl_N.md` shows tests passing
- Reviewer in lightweight mode for frontend: only checks files exist, doesn't run servers
- `write_file` on an existing source file warns (non-blocking) when the rewrite shrinks it >`DESTRUCTIVE_SHRINK_RATIO` (default 30%, only above `DESTRUCTIVE_SHRINK_MIN_LINES`=40 lines; shrink check covers `.py`/JS/TS plus `.go .rb .php .java .cs`) or drops top-level symbols (`def`/`class`/route decorators/JS exports; Python/JS/TS only, no size floor, `_top_level_symbols()` anchored to real column 0 — nested methods never count) — the warning names the removed symbols so the agent restores them next turn. `MINIMAL_DELTA_RULE` (shared, `agents/shared_rules.py`): never regenerate an existing file from memory, minimal delta over content actually read — interpolated into implementer AND e2e_tester (a deleted test never fails; the warning is the only signal)
- Bug-fix features require an executable repro script (`progress/repro_N.py`/`.sh`, fails while the bug exists) or an explicit `REPRO: NOT_FEASIBLE — <reason>` declaration; a spec with neither is quarantined (`.norepro`) and regenerated once, then falls back to annotate-and-continue. Root-cause claims are labeled CONFIRMED/HYPOTHESIS (unlabeled = HYPOTHESIS); CONFIRMED without an attached repro is auto-downgraded to HYPOTHESIS at implementer injection. Implementer runs the repro first (baseline) and last (fix confirmation)
- Convergence watchdog escalates: 2nd streak firing switches to imperative ("your NEXT tool call MUST be write_file"); `MAX_ITER_WITHOUT_WRITE` (default 40, 0 off) aborts an attempt early at N total iterations with zero writes — distinct message, digest still written, half the budget left for the informed retry
- Implementer max_iter → `progress/_investigation_impl_N.md` (files read, command outcomes, last hypothesis — deterministic, no LLM); next attempt gets it injected as "PREVIOUS ATTEMPT'S INVESTIGATION — historical context, not ground truth" (timestamped, trust it if the code hasn't changed since). Complements `tool_call_errors`, which only re-feeds errors (an attempt with 175 clean tool calls left the retry nothing). Deleted on feature approval and on a `wrong_premise` spec quarantine (misleading, not just stale); survives an ordinary final rejection (still useful for an imminent re-run)
- `run_repro_script` (tools.py): runs `progress/repro_N.py`/`.sh` on the HOST (like `run_playwright_tests`; `run_bash`'s docker sandbox can't reach the app and has no browser). Exposed to the implementer only on `e2e: true` bugfix features; path derived from the int-validated feature_id — no general host execution. Spec rule: browser-only repros must print the network trace of the failing action
- PREMISE CHECK EXIT (implementer, sanctioned — not a failure): if direct verification refutes the spec's diagnosis, write `PREMISE_CHECK: FAILED` in the impl report + `"premise_check": "failed"` in `impl_N.json` and end the attempt. `spawn_spec_writer` quarantines a cached spec on `diagnosis_N.json` `cause: wrong_premise` (external plugin, best-effort) or `premise_check: failed` (fallback: grep the `.md`), regenerating with the refutation injected as a constraint; a premise-check report is never reused by the impl cache even with `tests_passed: true`
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
