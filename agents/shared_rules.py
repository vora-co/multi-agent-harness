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
