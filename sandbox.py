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
      - default-deny network egress (see SANDBOX_NETWORK_MODE below)
    Falls back to "local" (with a loud warning, once per process) if no Docker
    daemon is reachable — so the harness keeps working for people without Docker,
    just less safely.

  "local" — today's behavior: subprocess.run(command, shell=True) on the host.
    Opt-in only. Intended for environments where Docker isn't available or
    wanted; clearly labeled as the less-safe path.

Network egress (SANDBOX_NETWORK_MODE env var)
----------------------------------------------
  "egress-proxy" (default) — default-deny allowlist:
      Sandboxed containers are attached only to an *internal* Docker network
      with no route to the outside world. The only thing on that network that
      can reach the internet is a small forward-proxy container
      (egress_proxy.py, see Dockerfile.proxy) that tunnels traffic on to its
      destination only when the hostname matches SANDBOX_EGRESS_ALLOWLIST —
      everything else gets a 403. This is enforced at the network boundary
      (the sandbox literally has no route around the proxy), not by trying to
      guess what an LLM-generated command might attempt.

  "bridge" — full outbound access, same as the host's network. Opt-out; useful
      if your features need to reach hosts outside the default allowlist and
      you don't want to maintain SANDBOX_EGRESS_ALLOWLIST.

  "none" — fully air-gapped, no network at all. The most restrictive option;
      use it for purely offline feature runs.

Both Docker/local backends expose `run(command, timeout, cwd, safe_write_dirs)
-> dict` with keys {stdout, stderr, returncode, success} or {error, ...},
matching what run_bash() returned before this change.
"""

import os
import json
import subprocess
import threading
import time
from abc import ABC, abstractmethod


def _sanitized_host_env() -> dict:
    """
    A copy of os.environ with every LLM-provider credential (DEEPSEEK_API_KEY,
    OPENAI_API_KEY, GROQ_API_KEY, CUSTOM_API_KEY, and any future <PROVIDER>_API_KEY
    added via LLM_FALLBACK_CHAIN — see harness.py's _build_provider_chain) stripped
    out. SANDBOX_MODE=local runs agent shell commands as a direct subprocess of this
    Python process; without this, subprocess.run() inherits the full host
    environment, so a command as innocuous as `env` or `printenv` would print the
    harness's own API keys straight into the tool result — which then gets logged.
    Docker mode doesn't have this problem: it only ever passes an explicit,
    harness-controlled `environment=` dict into the container (see
    DockerSandboxRunner.run below), never a copy of the host's.
    """
    return {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}

# ─── CONFIG (.env) ───────────────────────────────────────────────────────────

SANDBOX_MODE        = os.getenv("SANDBOX_MODE", "docker").strip().lower()
SANDBOX_IMAGE       = os.getenv("SANDBOX_IMAGE", "harness-sandbox:latest")
SANDBOX_MEM_LIMIT   = os.getenv("SANDBOX_MEM_LIMIT", "1g")          # docker mem_limit format
SANDBOX_CPU_LIMIT   = float(os.getenv("SANDBOX_CPU_LIMIT", "2"))    # number of CPUs
SANDBOX_PIDS_LIMIT  = int(os.getenv("SANDBOX_PIDS_LIMIT", "256"))   # max processes/threads
SANDBOX_KILL_GRACE  = 5                                              # extra seconds before force-kill

# "egress-proxy" (default, most secure) | "bridge" (full outbound) | "none" (air-gapped)
SANDBOX_NETWORK = os.getenv("SANDBOX_NETWORK_MODE", "egress-proxy").strip().lower()

# Default-deny egress allowlist used when SANDBOX_NETWORK_MODE=egress-proxy.
# Comma-separated hostnames; "*.example.com" matches the bare domain and any
# subdomain. Covers the package registries and git hosts most features need.
SANDBOX_EGRESS_ALLOWLIST = os.getenv(
    "SANDBOX_EGRESS_ALLOWLIST",
    "pypi.org,files.pythonhosted.org,"
    "registry.npmjs.org,registry.yarnpkg.com,"
    "github.com,*.github.com,raw.githubusercontent.com,*.githubusercontent.com,"
    "codeload.github.com,nodejs.org,*.nodesource.com,"
    "deb.debian.org,security.debian.org",
).strip()

PROXY_IMAGE   = os.getenv("SANDBOX_PROXY_IMAGE", "harness-egress-proxy:latest")
PROXY_NAME    = "harness-egress-proxy"     # container name, also its DNS name on PROXY_NETWORK
PROXY_NETWORK = "harness-sandbox-internal"  # internal Docker network (no route to the internet)
PROXY_PORT    = 3128

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
                timeout=timeout, cwd=cwd, env=_sanitized_host_env()
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

        # Network egress mode can fall back independently of the sandbox image
        # itself — e.g. if the daemon can't create internal networks for some
        # reason. Falling back to "none" (air-gapped) rather than "bridge" keeps
        # the safest-by-default posture: a broken proxy should never silently
        # turn into open egress.
        self._network_mode = SANDBOX_NETWORK
        if self._network_mode == "egress-proxy":
            try:
                self._ensure_proxy()
            except Exception as e:
                print(
                    f"⚠️  Could not start the egress allowlist proxy ({e}).\n"
                    "   Falling back to SANDBOX_NETWORK_MODE=none (fully air-gapped — the\n"
                    "   safest fallback). Set SANDBOX_NETWORK_MODE=bridge in .env if your\n"
                    "   features need open outbound access instead.\n"
                )
                self._network_mode = "none"

    def _ensure_proxy(self):
        """
        Set up the default-deny egress path:
          1. an *internal* Docker network with no route to the outside world
          2. a small forward-proxy container (egress_proxy.py) that is the
             only thing attached to BOTH that internal network and the default
             bridge — i.e. the only way out, and it only tunnels to hosts in
             SANDBOX_EGRESS_ALLOWLIST.
        Sandboxed containers then attach only to the internal network and get
        HTTP_PROXY/HTTPS_PROXY pointed at this container. Even a tool that
        ignores those env vars can't reach the internet directly — there's no
        route — so the allowlist is enforced at the network boundary either way.
        """
        self._ensure_proxy_network()
        self._ensure_proxy_image()
        self._ensure_proxy_container()

    def _ensure_proxy_network(self):
        try:
            self._client.networks.get(PROXY_NETWORK)
            return
        except Exception:
            pass
        self._client.networks.create(PROXY_NETWORK, driver="bridge", internal=True)

    def _ensure_proxy_image(self):
        try:
            self._client.images.get(PROXY_IMAGE)
            return
        except Exception:
            pass

        dockerfile_dir = os.path.dirname(os.path.abspath(__file__))
        if not os.path.exists(os.path.join(dockerfile_dir, "Dockerfile.proxy")):
            raise RuntimeError(
                f"Egress proxy image '{PROXY_IMAGE}' not found and no Dockerfile.proxy to build it from."
            )
        print(f"📦 Building egress proxy image '{PROXY_IMAGE}' (first run only)...")
        self._client.images.build(
            path=dockerfile_dir, dockerfile="Dockerfile.proxy", tag=PROXY_IMAGE, rm=True
        )
        print("✓ Egress proxy image ready.\n")

    def _ensure_proxy_container(self):
        try:
            existing = self._client.containers.get(PROXY_NAME)
            if existing.status != "running":
                existing.start()
            return
        except Exception:
            pass  # not found — create it below

        print(f"🌐 Starting egress allowlist proxy — allowed hosts: {SANDBOX_EGRESS_ALLOWLIST or '(none configured)'}")
        proxy = self._client.containers.run(
            PROXY_IMAGE,
            detach=True,
            name=PROXY_NAME,
            network=PROXY_NETWORK,
            environment={
                "EGRESS_ALLOWLIST": SANDBOX_EGRESS_ALLOWLIST,
                "EGRESS_PROXY_PORT": str(PROXY_PORT),
            },
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            restart_policy={"Name": "unless-stopped"},
        )
        # This is the ONE container allowed to also see the default bridge —
        # that's what gives it (and only it) a route to the internet.
        try:
            self._client.networks.get("bridge").connect(proxy)
        except Exception as e:
            try:
                proxy.remove(force=True)
            except Exception:
                pass
            raise RuntimeError(f"could not give the egress proxy internet access: {e}")

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

        run_kwargs = dict(
            detach=True,
            working_dir=workdir,
            volumes=volumes,
            tmpfs={"/tmp": "rw,size=512m,mode=1777"},
            read_only=True,
            user="1000:1000",
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            mem_limit=SANDBOX_MEM_LIMIT,
            nano_cpus=int(SANDBOX_CPU_LIMIT * 1_000_000_000),
            pids_limit=SANDBOX_PIDS_LIMIT,
        )

        if self._network_mode == "egress-proxy":
            # Default-deny egress: this network has no route to the internet —
            # the proxy is the only way out, and it tunnels only to allowlisted
            # hosts. Setting *_PROXY env vars makes well-behaved tools (pip, npm,
            # curl, git, apt) use it transparently; anything that ignores them
            # simply has no route and fails closed either way.
            proxy_url = f"http://{PROXY_NAME}:{PROXY_PORT}"
            no_proxy = "localhost,127.0.0.1,::1"
            run_kwargs["network"] = PROXY_NETWORK
            run_kwargs["environment"] = {
                "HTTP_PROXY": proxy_url, "HTTPS_PROXY": proxy_url,
                "http_proxy": proxy_url, "https_proxy": proxy_url,
                "NO_PROXY": no_proxy, "no_proxy": no_proxy,
            }
        else:
            # "bridge" (full outbound, opt-out) or "none" (fully air-gapped)
            run_kwargs["network_mode"] = self._network_mode

        container = None
        try:
            container = self._client.containers.run(
                SANDBOX_IMAGE,
                ["bash", "-lc", command],
                **run_kwargs,
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
