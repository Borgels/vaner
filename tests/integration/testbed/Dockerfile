# syntax=docker/dockerfile:1.7
#
# Vaner integration testbed (Phase D-1, CLI clients).
#
# Boots an Ubuntu 24.04 image with Vaner installed from the repo's
# current working tree plus the two CLI-only AI clients we currently
# support end-to-end:
#
#   - Claude Code  (Anthropic) — npm
#   - Codex CLI    (OpenAI)    — npm
#
# GUI clients (Cursor, Zed, VS Code, Continue, Cline, Windsurf,
# Claude Desktop, Roo) are out of scope for this image. Phase D-2
# will add an xvfb + noVNC variant for those.
#
# Build:
#   docker build -f tests/integration/testbed/Dockerfile -t vaner-testbed .
#
# Run a one-shot smoke:
#   docker run --rm vaner-testbed bash /opt/testbed/test-launch-clients.sh
#
# Drop into an interactive shell:
#   docker run --rm -it vaner-testbed bash

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    LC_ALL=C.UTF-8 \
    LANG=C.UTF-8 \
    PATH=/opt/uv/bin:/usr/local/bin:/usr/bin:/bin

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        jq \
        nodejs \
        npm \
        passwd \
        python3 \
        python3-pip \
        python3-venv \
        sudo \
    && rm -rf /var/lib/apt/lists/*

# uv as the Python install manager, pinned for reproducibility.
RUN curl -fsSL https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/opt/uv/bin sh

# Non-root user — testbed exercises per-user config dirs (~/.claude,
# ~/.codex, ~/.cursor, …) so root is the wrong identity for the run.
RUN /usr/sbin/useradd -m -s /bin/bash -u 1100 vaner \
    && echo 'vaner ALL=(ALL) NOPASSWD: ALL' >> /etc/sudoers

# Install the two CLI AI clients globally. Pinned majors only — minor
# bumps are fine, but a 2.x → 3.x change should be an explicit update.
RUN npm install -g \
        '@anthropic-ai/claude-code@^2' \
        '@openai/codex@^0.50' \
    && npm cache clean --force

USER vaner
WORKDIR /home/vaner

# Bring in the Vaner source — bind-mounted at run time would be nicer
# for iteration, but COPY keeps the image self-contained for one-shot
# smoke runs in CI.
COPY --chown=vaner:vaner . /opt/vaner

RUN cd /opt/vaner \
    && uv venv /home/vaner/.venv \
    && . /home/vaner/.venv/bin/activate \
    && uv pip install -e .

ENV PATH=/home/vaner/.venv/bin:$PATH

# Sample repo the testbed runs `vaner launch` against. Empty enough
# to be predictable, real enough to look like a repo.
COPY --chown=vaner:vaner tests/integration/testbed/sample-repo /home/vaner/sample-repo

# Smoke-test script + entrypoint helpers.
COPY --chown=vaner:vaner tests/integration/testbed/test-launch-clients.sh /opt/testbed/test-launch-clients.sh
RUN sudo chmod +x /opt/testbed/test-launch-clients.sh

WORKDIR /home/vaner/sample-repo
CMD ["bash"]
