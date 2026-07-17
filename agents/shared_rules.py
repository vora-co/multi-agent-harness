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

MINIMAL_DELTA_RULE = """
MINIMAL-DELTA REWRITES (mandatory for every write_file to a file that already exists):
read_file it first (in this run) and rewrite it as the minimal delta over that real
content — every line you were not deliberately changing must remain byte-identical to
what you read. NEVER regenerate an existing file from memory or from general knowledge
of what it "should" contain. Real incident (feature #77, attempt 2): a single
write_file regenerated a ~750-line router from memory — inventing an import from a
module that doesn't exist, deleting an entire POST endpoint, a security
model_validator, and the await session.commit() call — and the identical regression
from the previous round had already reached origin/main. In test files this failure
mode is even quieter: a deleted test never fails — coverage just shrinks silently, and
the write_file warning naming the removed def test_* symbols is the only signal anyone
will ever get. If a write_file result comes back with a "warning" field naming removed
symbols, treat it as ground truth about your own write: before doing anything else,
restore every listed symbol you did not intend to delete — from the content you read
earlier this run, or, if the file already existed in the last commit, via
run_bash("git diff -- <path>") / run_bash("git show HEAD:<path>").
"""

DB_CONNECTED_TEST_RULE = """
DATABASE-CONNECTED TEST DETECTION (mandatory, applies to every test file you are about to
run): before running any test file with a plain run_bash pytest invocation, check whether
it imports asyncpg directly or defines/calls a `_dsn(` connection helper (grep for
`import asyncpg` or `_dsn(` in the file — this is a content signal, not a specific
filename; any test file matching it counts, not just a currently-known one). If it
matches, you MUST run that file via run_backend_pytest(test_path=...) instead of
run_bash — never run_bash for a file matching this signature.
- WHY THIS IS MANDATORY, NOT ADVISORY: run_bash executes inside an isolated sandbox
  container with NO route to this project's Postgres/backend compose services. A test that
  opens a real asyncpg connection run through run_bash WILL ALWAYS fail with a connection
  error (e.g. "Connect call failed ('127.0.0.1', 5432)") no matter what the code under test
  actually does — that failure carries ZERO information about correctness, in either
  direction. run_backend_pytest runs on the host via docker exec into the project's real
  backend container (starting the backend+postgres compose services itself first if
  they're not already up), so it gets an actual Postgres connection instead of a
  guaranteed sandbox artifact.
- If run_backend_pytest is not present in your toolset when you need it, do NOT fall back
  to run_bash for a DB-connecting test file — note in your report that this test's
  coverage could not be executed and why (the harness exposes the tool automatically once
  it detects this feature touches backend/tests/; its absence means that detection didn't
  fire for this feature).
- RESIDUAL CONNECTION FAILURE (mandatory, deterministic — this is not a judgment call): if
  a test still fails with "Connect call failed" or an equivalent connection-refused/timeout
  error AFTER being run via run_backend_pytest, that failure is neither an acceptable
  environmental exception to wave through NOR a code defect to blame on the implementation
  — it means the postgres/backend compose containers are not actually reachable in this
  environment even though run_backend_pytest starts them itself. Real incident: two
  structurally identical features got opposite verdicts from two separate reviewer runs on
  the same kind of failure — one silently approved, one rejected — because nothing gave
  either of them a deterministic rule for this exact case. This paragraph is that rule:
  state explicitly, in whatever report or verdict you produce, that this is a genuine
  environment setup problem that needs human investigation — never silently approve past
  it, and never spend further attempts trying to "fix" application code in response to it.
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
- If the task description contains a diagnosis block labeled CONFIRMED — exact
  file, exact defect, exact fix, AND an attached executable reproduction
  (e.g. progress/repro_<feature_id>.py/.sh) showing how it was verified —
  apply that fix directly. Do not independently re-litigate whether the
  diagnosis is correct by re-running exploratory commands; that diagnosis
  already cost a prior attempt's full iteration budget to produce. Spend your
  budget applying and verifying it, not re-discovering it.
- If a diagnosis is labeled HYPOTHESIS — or carries no label at all, which
  means exactly the same thing — treat it as a lead, not a verdict, no matter
  how confident its prose sounds. Spend 3-5 iterations verifying it in the
  most direct way available (run the attached repro script if there is one;
  otherwise hit the endpoint directly with curl/httpx and query the DB
  directly) BEFORE committing the rest of your budget to that location.
  Real incident (feature #77): a spec asserted a backend persistence bug in
  confident prose with no reproduction attached; the implementer "applied the
  diagnosis directly" and burned 2 rounds × 2 attempts × 80 iterations in a
  layer that was working correctly — a 5-minute direct check (curl + DB
  query) would have shown the real cause was response-array reordering
  (ORDER BY ... is_active DESC) plus a frontend rendering by array position.
  Prose certainty is not evidence; only a reproduction is.
- If a message during this run tells you that you appear to be exploring without
  writing anything, treat it as ground truth about your own trajectory, not a
  false positive to argue with — stop and make the smallest change that
  addresses the task with what you already know.
"""
