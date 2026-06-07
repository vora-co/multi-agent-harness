"""
sandbox.py — Pluggable execution backends for tools.run_bash().

Why this exists
---------------
`run_bash()` previously ran agent-issued shell commands directly on the host via
`subprocess.run(shell=True)`, gated only by a regex blocklist. That is trivially
bypassable (e.g. `python3 -c "open('/etc/hosts','w').write(...)"`) and gives
LLM-generated code the full privileges of whoever runs the harness. This module
replaces that with a pluggable `SandboxRunner` so agent commands execute inside
an isolated container by default, while keeping `run_bash()`'s exact signature
and JSON return shape — nothing else in the pipeline changes.

Modes (SANDBOX_MODE env var)
----------------------------
  "docker" (default) — one container per command:
      - read-only root filesystem
      - only SAFE_WRITE_DIRS bind-mounted read-write; everything else in the
        project is mounted read-only (or not visible at all outside it)
      - non-root user, all Linux capabilities dropped, default seccomp profile
      - memory / CPU / PID limits
      - a wall-clock kill switch independent of the in-container command timeout
    Falls back to "local" (with a loud warning, once per process) if no Docker
    daemon is reachable — so the harness keeps working for people without Docker,
    just less safely.

  "local" — today's behavior: subprocess.run(command, shell=True) on the host.
    Opt-in only. Intended for environments where Docker isn't available or
    wanted; clearly labeled as the less-safe path.

Both backends expose `run(command, timeout, cwd, safe_write_dirs) -> dict` with
keys {stdout, stderr, returncode, success} or {error, ...}, matching what
run_bash() returned before this change.
"""

import os
import json
import subprocess
import threading
import time
from abc import ABC, abstractmethod

# ─── CONFIG (.env) ───────────────────────────────────────────────────────────

SANDBOX_MODE        = os.getenv("SANDBOX_MODE", "docker").strip().lower()
SANDBOX_IMAGE       = os.getenv("SANDBOX_IMAGE", "harness-sandbox:latest")
SANDBOX_MEM_LIMIT   = os.getenv("SANDBOX_MEM_LIMIT", "1g")          # docker mem_limit format
SANDBOX_CPU_LIMIT   = float(os.getenv("SANDBOX_CPU_LIMIT", "2"))    # number of CPUs
SANDBOX_PIDS_LIMIT  = int(os.getenv("SANDBOX_PIDS_LIMIT", "256"))   # max processes/threads
SANDBOX_NETWORK     = os.getenv("SANDBOX_NETWORK_MODE", "bridge")   # "bridge" | "none"
SANDBOX_KILL_GRACE  = 5                                              # extra seconds before force-kill

_warned_fallback = False  # only print the "running unsandboxed" banner once


def _warn_fallback(reason: str):
    global _warned_fallback
    if not _warned_fallback:
        print(
            "\n⚠️  SANDBOX DISABLED — running agent shell commands directly on the host.\n"
            f"   Reason: {reason}\n"
            "   This means LLM-generated code runs with your full user privileges.\n"
            "   Install Docker Desktop / OrbStack / Colima and restart to sandbox it,\n"
            "   or set SANDBOX_MODE=local in .env to silence this warning.\n"
        )
        _warned_fallback = True


# ─── INTERFACE ───────────────────────────────────────────────────────────────

class SandboxRunner(ABC):
    """Common interface for all execution backends used by run_bash()."""

    @abstractmethod
    def run(self, command: str, timeout: int, cwd: str, safe_write_dirs: tuple) -> dict:
        """
        Execute `command` and return a dict with either:
          {"stdout": str, "stderr": str, "returncode": int, "success": bool}
        or:
          {"error": str, ...}
        """
        raise NotImplementedError


# ─── BACKEND: today's behavior, unchanged ────────────────────────────────────

class LocalSubprocessRunner(SandboxRunner):
    """Runs the command directly on the host. This is the pre-sandbox behavior,
    kept as an explicit opt-in (SANDBOX_MODE=local) and as the automatic
    fallback when no container runtime is available."""

    def run(self, command: str, timeout: int, cwd: str, safe_write_dirs: tuple) -> dict:
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=cwd
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
                "success": result.returncode == 0,
            }
        except subprocess.TimeoutExpired:
            return {"error": f"Timeout after {timeout}s", "timeout": True}
        except Exception as e:
            return {"error": str(e)}


# ─── BACKEND: per-command Docker container ───────────────────────────────────

class DockerSandboxRunner(SandboxRunner):
    """
    Runs each command inside a short-lived, locked-down Docker container.

    Filesystem confinement is enforced by the mount layout itself (the
    container literally cannot see paths outside what we mount), which closes
    the write-confinement bypass at the OS boundary instead of trying to parse
    arbitrary shell commands in Python:

      /workspace              → project root, mounted READ-ONLY
      /workspace/<safe_dir>   → each entry in SAFE_WRITE_DIRS, remounted READ-WRITE
      /tmp                    → ephemeral tmpfs, READ-WRITE (tools need scratch space)

    Everything else (root fs, host filesystem, host network namespace) is
    inaccessible from inside the container.
    """

    def __init__(self):
        import docker  # imported lazily so `docker` is only a hard dependency in this mode
        from docker.errors import DockerException
        self._docker_errors = DockerException
        try:
            self._client = docker.from_env()
            self._client.ping()
        except Exception as e:
            raise RuntimeError(f"Docker daemon not reachable: {e}")

        self._ensure_image()

    def _ensure_image(self):
        """Build the sandbox image from the bundled Dockerfile if it isn't present yet."""
        try:
            self._client.images.get(SANDBOX_IMAGE)
            return
        except Exception:
            pass

        dockerfile_dir = os.path.dirname(os.path.abspath(__file__))
        if not os.path.exists(os.path.join(dockerfile_dir, "Dockerfile")):
            raise RuntimeError(
                f"Sandbox image '{SANDBOX_IMAGE}' not found and no Dockerfile to build it from. "
                "Run `bash init.sh` first, or set SANDBOX_MODE=local."
            )
        print(f"📦 Building sandbox image '{SANDBOX_IMAGE}' (first run only — this can take a minute)...")
        self._client.images.build(path=dockerfile_dir, tag=SANDBOX_IMAGE, rm=True)
        print("✓ Sandbox image ready.\n")

    def run(self, command: str, timeout: int, cwd: str, safe_write_dirs: tuple) -> dict:
        project_root = os.path.abspath(cwd or os.getcwd())
        workdir = "/workspace"

        # Mount the project read-only, then re-mount each SAFE_WRITE_DIRS entry
        # read-write on top — Docker resolves overlapping bind mounts by
        # specificity, so the more specific (rw) mount wins for its subtree.
        volumes = {project_root: {"bind": workdir, "mode": "ro"}}
        for d in safe_write_dirs:
            host_path = os.path.normpath(os.path.join(project_root, d))
            if not host_path.startswith(project_root):
                continue  # never mount anything outside the project root as rw
            os.makedirs(host_path, exist_ok=True)
            volumes[host_path] = {"bind": f"{workdir}/{d.rstrip('/')}", "mode": "rw"}

        container = None
        try:
            container = self._client.containers.run(
                SANDBOX_IMAGE,
                ["bash", "-lc", command],
                detach=True,
                working_dir=workdir,
                volumes=volumes,
                tmpfs={"/tmp": "rw,size=512m,mode=1777"},
                read_only=True,
                user="1000:1000",
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                network_mode=SANDBOX_NETWORK,
                mem_limit=SANDBOX_MEM_LIMIT,
                nano_cpus=int(SANDBOX_CPU_LIMIT * 1_000_000_000),
                pids_limit=SANDBOX_PIDS_LIMIT,
            )

            timed_out = self._wait_with_kill_switch(container, timeout)

            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", "replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", "replace")

            if timed_out:
                return {"error": f"Timeout after {timeout}s", "timeout": True, "stdout": stdout, "stderr": stderr}

            try:
                container.reload()
                returncode = container.attrs["State"]["ExitCode"]
            except Exception:
                returncode = -1

            return {
                "stdout": stdout,
                "stderr": stderr,
                "returncode": returncode,
                "success": returncode == 0,
            }
        except self._docker_errors as e:
            return {"error": f"Sandbox error: {e}"}
        except Exception as e:
            return {"error": str(e)}
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass

    def _wait_with_kill_switch(self, container, timeout: int) -> bool:
        """
        Blocks until the container exits or `timeout` elapses, whichever first.
        Returns True if we had to kill it (wall-clock exceeded). This is a
        watchdog independent of any timeout the command itself might set —
        a runaway loop inside the container cannot outlive it.
        """
        done = threading.Event()

        def _waiter():
            try:
                container.wait(timeout=timeout + SANDBOX_KILL_GRACE + 30)
            except Exception:
                pass
            finally:
                done.set()

        t = threading.Thread(target=_waiter, daemon=True)
        t.start()
        finished = done.wait(timeout)
        if finished:
            return False

        # Wall clock exceeded — kill it.
        try:
            container.kill()
        except Exception:
            pass
        done.wait(SANDBOX_KILL_GRACE)
        return True


# ─── RUNNER SELECTION (cached singleton) ─────────────────────────────────────

_runner = None
_runner_lock = threading.Lock()


def get_runner() -> SandboxRunner:
    """
    Returns the process-wide SandboxRunner, selected once based on SANDBOX_MODE
    (with automatic, warned fallback to local execution if Docker isn't usable).
    """
    global _runner
    if _runner is not None:
        return _runner

    with _runner_lock:
        if _runner is not None:  # re-check after acquiring the lock
            return _runner

        if SANDBOX_MODE == "local":
            _warn_fallback("SANDBOX_MODE=local was set explicitly in .env")
            _runner = LocalSubprocessRunner()
        else:
            try:
                _runner = DockerSandboxRunner()
            except Exception as e:
                _warn_fallback(str(e))
                _runner = LocalSubprocessRunner()

        return _runner
