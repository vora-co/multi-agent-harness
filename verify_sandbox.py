"""
verify_sandbox.py — quick manual check that SANDBOX_MODE=docker is actually
isolating run_bash (filesystem + home dir), and that the default-deny egress
proxy (SANDBOX_NETWORK_MODE=egress-proxy) lets allowlisted hosts through while
blocking everything else — not just that the images were built.

Run from the project root:
    python3 verify_sandbox.py

Safe to delete afterwards — this is a one-off diagnostic, not part of the harness.
"""
import json
import socket
import tools
from sandbox import get_runner

print("=" * 60)
print("Test 1 — which runner is active?")
print("=" * 60)
runner = get_runner()
name = type(runner).__name__
print(f"Active runner: {name}")
if name == "DockerSandboxRunner":
    print("✓ PASS — sandbox is active")
else:
    print("✗ Running in LocalSubprocessRunner — sandbox is NOT active.")
    print("  Check the warning banner that printed above for why it fell back.")

print()
print("=" * 60)
print("Test 2 — does the command run in an isolated environment?")
print("=" * 60)
print(f"Host hostname:      {socket.gethostname()}")
res = json.loads(tools.run_bash("echo \"container hostname: $(hostname)\"; echo \"user: $(whoami)\"; echo \"cwd: $(pwd)\""))
print(f"run_bash output:\n{res.get('stdout', res)}")
print("✓ PASS if the hostname above differs from your Mac's hostname and cwd is /workspace")

print()
print("=" * 60)
print("Test 3 — can it write inside SAFE_WRITE_DIRS? (should succeed)")
print("=" * 60)
res = json.loads(tools.run_bash("echo 'sandbox-write-test-ok' > progress/_sandbox_test.txt && cat progress/_sandbox_test.txt"))
print(json.dumps(res, indent=2))
print("✓ PASS if success=true and you see 'sandbox-write-test-ok'")
print("  (check on your Mac: cat progress/_sandbox_test.txt — it should be there, written by the container)")

print()
print("=" * 60)
print("Test 4 — can it write OUTSIDE SAFE_WRITE_DIRS? (should FAIL)")
print("=" * 60)
res = json.loads(tools.run_bash("echo 'should never appear' > /etc/hosts; echo \"exit=$?\"; cat /etc/hosts"))
print(json.dumps(res, indent=2))
print("✓ PASS if the write fails (Read-only file system) — /etc/hosts shown is the")
print("  CONTAINER's own file, not your Mac's (compare with: cat /etc/hosts on your Mac)")

print()
print("=" * 60)
print("Test 5 — can it see your home directory / SSH keys? (should NOT)")
print("=" * 60)
res = json.loads(tools.run_bash("echo \"home=$HOME\"; ls -la $HOME 2>&1 | head -5; cat ~/.ssh/id_rsa 2>&1 | head -1"))
print(json.dumps(res, indent=2))
print("✓ PASS if $HOME is something like /home/sandbox (empty), NOT your Mac user folder,")
print("  and the .ssh/id_rsa read fails with 'No such file or directory'")

print()
print("=" * 60)
print("Test 6 — egress allowlist: can it reach an ALLOWED host? (should succeed)")
print("=" * 60)
from sandbox import SANDBOX_NETWORK
if SANDBOX_NETWORK != "egress-proxy":
    print(f"  SKIPPED — SANDBOX_NETWORK_MODE={SANDBOX_NETWORK!r}, not 'egress-proxy'.")
else:
    res = json.loads(tools.run_bash(
        "curl -s -o /dev/null -w 'http_code=%{http_code}\\n' --max-time 20 https://pypi.org/simple/ || echo CURL_FAILED"
    ))
    print(json.dumps(res, indent=2))
    print("✓ PASS if you see 'http_code=200' (or similar 2xx/3xx) — pypi.org is in the")
    print("  default SANDBOX_EGRESS_ALLOWLIST and the proxy let the connection through")

print()
print("=" * 60)
print("Test 7 — egress allowlist: can it reach a NON-ALLOWED host? (should FAIL)")
print("=" * 60)
if SANDBOX_NETWORK != "egress-proxy":
    print(f"  SKIPPED — SANDBOX_NETWORK_MODE={SANDBOX_NETWORK!r}, not 'egress-proxy'.")
else:
    res = json.loads(tools.run_bash(
        "curl -s -o /dev/null -w 'http_code=%{http_code}\\n' --max-time 20 https://example.com/ || echo CURL_FAILED"
    ))
    print(json.dumps(res, indent=2))
    print("✓ PASS if the request fails or returns a non-2xx code — example.com is NOT in")
    print("  the allowlist, so the proxy returned 403 (or there's simply no route to it)")

print()
print("=" * 60)
print("Cleanup")
print("=" * 60)
import os
test_file = "progress/_sandbox_test.txt"
if os.path.exists(test_file):
    os.remove(test_file)
    print(f"Removed {test_file}")
print("Done. You can delete verify_sandbox.py now.")
