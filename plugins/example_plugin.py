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

  after_feature_approved(feature_id, description, attempts)
      Fired when the Reviewer approves a feature.
      Use for: opening a Git PR, notifying Slack, updating a project tracker.

  after_feature_failed(feature_id, description, attempts, final_verdict)
      Fired when a feature exhausts all retries and is marked failed.
      Use for: alerting, escalation, writing a failure report.

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


# ── Example 5: send a session cost report ────────────────────────────────────

def on_after_session(session_costs: dict, **kwargs):
    """Called once when the harness exits. session_costs mirrors session_costs.json."""
    # totals = session_costs.get("totals", {})
    # usd = totals.get("estimated_usd", 0)
    # print(f"[example_plugin] session total: USD {usd:.4f}")
    pass

# register_hook("after_session", on_after_session)
