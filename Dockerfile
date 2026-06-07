# Sandbox image for multi-agent-harness — locked-down runtime for agent-issued
# shell commands (see sandbox.py / SANDBOX_MODE=docker, the default).
#
# Bakes in the same stack the generated apps expect (Python 3.11 + Node 18)
# so `pytest`, `npm install`, `playwright`, etc. all work inside the sandbox
# exactly as they would on the host — no network access needed at run time
# just to run the test suite.
#
# Built automatically on first run by sandbox.py (DockerSandboxRunner._ensure_image)
# or explicitly via: docker build -t harness-sandbox:latest .

FROM python:3.11-slim

# bash (run_bash uses `bash -lc`), git/curl (common in generated test/build steps),
# and Node.js 18 (frontend features run npm install/build/test inside the sandbox).
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash curl ca-certificates git \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Pre-install the harness/generated-app Python deps so a fresh sandbox can run
# `pytest` etc. without needing egress on every container start.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Playwright's browser binaries + OS-level deps need root to install.
RUN python3 -m playwright install --with-deps chromium || true

# Non-root user — must match the UID:GID sandbox.py launches containers as (1000:1000),
# since the bind-mounted SAFE_WRITE_DIRS are owned by the host user and we want
# writes from inside the container to land with sane ownership outside it too.
RUN groupadd -g 1000 sandbox \
    && useradd -m -u 1000 -g 1000 sandbox \
    && chown -R sandbox:sandbox /home/sandbox

USER sandbox
ENV HOME=/home/sandbox

ENTRYPOINT []
CMD ["bash"]
