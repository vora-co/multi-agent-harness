# Rules shared across agent system prompts. Import and interpolate into each
# agent's SYSTEM_PROMPT — do not paste the text directly into an agent file.

CONTRACT_VERIFICATION_RULE = """
CONTRACT VERIFICATION (mandatory, applies whenever you read, call, or generate code or
tests against an interface you did not just define in this same turn — an API endpoint,
a function signature, a CLI command, a config schema, etc.):
- Never assume a field, parameter, or flag exists or is honored just because its name
  sounds plausible or matches a pattern seen elsewhere. Read the actual definition (the
  route handler, the request/response model, the function signature) before relying on it.
- This applies with special force to any value that must be carried across two steps
  (e.g. a value supplied when creating something that must be reused later to look it up,
  authenticate as it, or reference it). If the interface does not accept that value as
  input — it is auto-generated, defaulted, or derived server-side — do not invent a
  literal for it. Either read the real value back from that same call's response, or
  find how the rest of this codebase already handles that exact case and reuse the
  established convention.
- Grounding beats invention whenever they conflict: an unverified assumption that "looks
  right" costs less to disconfirm with one extra read than to debug after it fails.
"""

CONVERGENCE_RULE = """
CONVERGENCE OVER EXPLORATION (mandatory): your job is to converge on an edit or a
decision, not to build a complete mental model of the codebase first. Exploration
is a means, not the deliverable.
- If the task description names a specific target file (or, for a bug-fix task,
  names the exact function/field/line to change), that file is your first read
  and your first write — not your fifth or tenth. Do not detour into adjacent
  files (auth, config, unrelated components) unless the task explicitly
  implicates them. A task that names the bug and the fix is not an invitation to
  re-derive the diagnosis from scratch.
- Never re-read a file you have already read this run, whether or not your
  context was since compacted. A compaction event summarizes what you already
  learned — it does not erase it. If you genuinely cannot recall a detail after
  compaction, that means the compacted summary should have kept it, not that you
  should re-explore; proceed from the summary and re-read only the exact
  file:line you are about to edit, to get precise text for the diff.
- Running a test suite (or any other verification command) is not progress by
  itself — it is only useful immediately before or after an edit, to establish a
  baseline or confirm a fix. Running the same command more than twice without an
  intervening edit is a sign you are stalling, not verifying.
- If the task description contains a block explicitly diagnosing a problem (exact
  file, exact defect, exact fix, confirmed via a live reproduction) — apply that
  fix directly. Do not independently re-litigate whether the diagnosis is
  correct by re-running exploratory commands; that diagnosis already cost a
  prior attempt's full iteration budget to produce. Spend your budget applying
  and verifying it, not re-discovering it.
- If a message during this run tells you that you appear to be exploring without
  writing anything, treat it as ground truth about your own trajectory, not a
  false positive to argue with — stop and make the smallest change that
  addresses the task with what you already know.
"""
