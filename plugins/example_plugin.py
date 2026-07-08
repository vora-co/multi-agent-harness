"""
example_plugin.py — starter template for harness plugins.

Copy this file, rename it (e.g. my_plugin.py), implement the hooks you need,
and uncomment the register_hook() calls. The harness auto-loads every *.py file
in this directory at startup — no other wiring needed.

Files whose names start with _ are skipped by the loader, so you can use
_disabled_plugin.py to park code that isn't ready yet.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AVAILABLE EVENTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  before_feature(feature_id, description, e2e)
      Fired at the start of each feature cycle, before spec or code is written.
      Use for: external logging, locking a Jira ticket, pre-flight checks.

  after_spec_generated(feature_id, spec_path, issues)
      Fired after the Spec Writer finishes and validation runs.
      issues: list[str] — empty if validation found nothing.
      Use for: enriching the spec with external context, posting a spec review.

  before_approval_finalized(feature_id, description, attempt, review_result)
      Fired right after the Reviewer returns an APPROVED verdict, BEFORE the
      harness commits to it. This is the ONE event where a callback can change
      the outcome instead of just observing it:
        - return None / anything falsy  -> "no opinion", approval proceeds
        - return {"block": True, "reason": "..."} -> veto the approval
      A veto is folded into the normal rejection/retry flow — same retry loop,
      same eventual after_feature_failed if retries run out. No new states.
      Dispatched with _fire_gate(), not _fire() — register normally via
      register_hook(), the harness picks the right dispatcher for you.
      Use for: hard pre-merge gates (SAST/secret scanning, policy checks)
      that must block a bad approval rather than just react to it afterward.

  after_feature_approved(feature_id, description, attempts)
      Fired when the Reviewer approves a feature (and no plugin vetoed it
      via before_approval_finalized).
      Use for: opening a Git PR, notifying Slack, updating a project tracker.

  after_feature_failed(feature_id, description, attempts, final_verdict)
      Fired when a feature exhausts all retries and is marked failed.
      Use for: alerting, escalation, writing a failure report.

  after_reviewer_rejected(feature_id, description, attempt, max_attempts, rejection_reason)
      Fired every time the Reviewer rejects a feature cycle, including
      attempts that will still be retried — unlike after_feature_failed,
      which only fires once retries are exhausted. Does NOT fire for a
      before_approval_finalized veto or an E2E failure — this is scoped to
      a genuine Reviewer rejection only.
      Use for: per-attempt diagnostics, root-cause logging, feeding a
      failure-analysis pipeline without waiting for the feature to fully fail.

  before_spawn_agent(role, system_prompt, task, feature_id)
      Fired immediately before each agent is invoked — once per agent call.
      role: "spec_writer" | "implementer" | "reviewer" | "e2e_tester"
      system_prompt: the prompt that would be sent as-is
      task: the user-turn task string (may include stack-specific commands
            like "run pytest" or "run the dev server with uvicorn")
      feature_id: current feature being processed
      This is a TRANSFORM hook — what you return matters:
        - return None / {} / falsy  → no change, originals pass through
        - return {"system_prompt": "...", "task": "..."} → override one or both
      Callbacks chain: each sees the output of the previous.
      Use for: injecting stack-specific context, replacing test runner commands,
               adapting prompts for non-default technology stacks.

  after_session(session_costs)
      Fired once when the harness exits (including on crash).
      session_costs: dict — same structure as progress/session_costs.json.
      Use for: billing, analytics, end-of-session summaries.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. Always add **kwargs to every callback signature. New arguments may be
     added to events in future versions — **kwargs keeps your plugin compatible.

  2. Keep callbacks fast and non-blocking. They run synchronously in the
     main thread. For slow operations (HTTP requests, file uploads), spawn
     a background thread or use asyncio.

  3. Never import from plugins/ inside harness.py — the dependency is
     one-directional: plugins import from harness, not the other way around.

  4. Errors inside callbacks are caught by the harness and logged; they do
     not stop the pipeline. Still, write defensive code.
"""

from harness import register_hook


# ── Example 1: log to an external system before each feature ─────────────────

def on_before_feature(feature_id: int, description: str, e2e: bool, **kwargs):
    """Called at the start of every feature cycle."""
    # Example: write to an external log or lock a ticket
    # requests.post(os.environ["LOG_ENDPOINT"], json={"feature_id": feature_id})
    pass

# register_hook("before_feature", on_before_feature)


# ── Example 2: post a Slack notification when a feature is approved ───────────

def on_feature_approved(feature_id: int, description: str, attempts: int, **kwargs):
    """Called when the Reviewer approves a feature."""
    # import requests, os
    # requests.post(os.environ["SLACK_WEBHOOK_URL"], json={
    #     "text": (
    #         f"✅ Feature #{feature_id} approved in {attempts} attempt(s)\n"
    #         f"_{description[:80]}_"
    #     )
    # })
    pass

# register_hook("after_feature_approved", on_feature_approved)


# ── Example 3: open a GitHub PR after a feature is approved ──────────────────

def on_feature_approved_pr(feature_id: int, description: str, attempts: int, **kwargs):
    """Create a pull request for the approved feature via GitHub API."""
    # import subprocess, os, requests
    # branch = f"feature/{feature_id}-auto"
    # subprocess.run(["git", "checkout", "-b", branch], check=True)
    # subprocess.run(["git", "add", "-A"], check=True)
    # subprocess.run(["git", "commit", "-m", f"feat: feature #{feature_id} — {description[:60]}"], check=True)
    # subprocess.run(["git", "push", "-u", "origin", branch], check=True)
    # requests.post(
    #     f"https://api.github.com/repos/{os.environ['GITHUB_REPO']}/pulls",
    #     headers={"Authorization": f"token {os.environ['GITHUB_TOKEN']}"},
    #     json={"title": f"Feature #{feature_id}", "head": branch, "base": "main", "body": description},
    # )
    pass

# register_hook("after_feature_approved", on_feature_approved_pr)


# ── Example 3b: hard-gate an approval before it's finalized ──────────────────

def on_before_approval_finalized(feature_id: int, description: str,
                                 attempt: int, review_result: str, **kwargs):
    """
    Called right after the Reviewer says APPROVED, before the harness commits
    to it. Unlike the other events, what you return matters:
      - return None (or anything falsy) to let the approval proceed
      - return {"block": True, "reason": "..."} to veto it — the harness will
        treat this exactly like a Reviewer rejection (retry, then eventually
        after_feature_failed if retries run out)
    Keep this fast — it runs synchronously and blocks the verdict.
    """
    # Example: run a quick policy/secret check on the changed files and veto
    # if something looks wrong.
    # if _looks_dangerous(feature_id):
    #     return {"block": True, "reason": "Hardcoded credential detected in diff"}
    return None

# register_hook("before_approval_finalized", on_before_approval_finalized)


# ── Example 4: alert on failure ───────────────────────────────────────────────

def on_feature_failed(feature_id: int, description: str,
                      attempts: int, final_verdict: str, **kwargs):
    """Called when a feature is marked failed after all retries."""
    # import requests, os
    # requests.post(os.environ["SLACK_WEBHOOK_URL"], json={
    #     "text": f"❌ Feature #{feature_id} failed after {attempts} attempt(s): {final_verdict[:120]}"
    # })
    pass

# register_hook("after_feature_failed", on_feature_failed)


# ── Example 4b: log every individual Reviewer rejection ──────────────────────

def on_reviewer_rejected(feature_id: int, description: str, attempt: int,
                         max_attempts: int, rejection_reason: str, **kwargs):
    """Called on every Reviewer rejection, including ones that will retry."""
    # print(f"[example_plugin] feature #{feature_id} rejected on attempt "
    #       f"{attempt}/{max_attempts}: {rejection_reason[:120]}")
    pass

# register_hook("after_reviewer_rejected", on_reviewer_rejected)


# ── Example 5: override stack context before each agent ──────────────────────

def on_before_spawn_agent(role: str, system_prompt: str, task: str,
                          feature_id: int, **kwargs):
    """
    Called before every agent invocation. Return a dict to override
    system_prompt and/or task, or return None to leave them unchanged.

    Example: replace the default FastAPI+React context with a custom stack.
    """
    # Example: inject a custom architecture block for a .NET Core + Angular stack
    # new_prompt = system_prompt.replace(
    #     "Stack: FastAPI + React + Tailwind + JSON",
    #     "Stack: .NET Core 8 (C#) + Angular 17 + PostgreSQL",
    # )
    # if role == "reviewer":
    #     new_task = task.replace("python3 -m pytest", "dotnet test")
    #     return {"system_prompt": new_prompt, "task": new_task}
    # return {"system_prompt": new_prompt}
    return None

# register_hook("before_spawn_agent", on_before_spawn_agent)


# ── Example 6: send a session cost report ────────────────────────────────────

def on_after_session(session_costs: dict, **kwargs):
    """Called once when the harness exits. session_costs mirrors session_costs.json."""
    # totals = session_costs.get("totals", {})
    # usd = totals.get("estimated_usd", 0)
    # print(f"[example_plugin] session total: USD {usd:.4f}")
    pass

# register_hook("after_session", on_after_session)
