# Vaner integration testbed

A reproducible Ubuntu container with Vaner installed from this repo's
working tree plus the supported AI clients. The point is to exercise
`vaner launch <client>` end-to-end against real client tooling
without polluting the host machine.

## Phase D-1 — CLI clients (this image)

Two clients are pre-installed:

| Client       | How             |
|--------------|-----------------|
| Claude Code  | `npm i -g @anthropic-ai/claude-code` |
| Codex CLI    | `npm i -g @openai/codex` |

The smoke script (`test-launch-clients.sh`) runs `vaner launch` for
each and asserts all four leverage layers landed:

- MCP entry — `~/.claude/mcp.json`, `~/.codex/config.toml`
- Primer — `.claude/CLAUDE.md`, `AGENTS.md`
- Skill — `~/.claude/skills/vaner/vaner-feedback/SKILL.md`,
  `~/.codex/skills/vaner-feedback/SKILL.md`
- Plugin / hooks — `~/.claude/plugins/vaner/` (Codex's plugin gate is
  still flag-locked upstream so we don't assert it here)

## Phase D-2 — GUI testbed (Vaner Desktop, future GUI clients)

`Dockerfile.gui` boots a real Ubuntu 24.04 desktop session
(`ubuntu-desktop-minimal` → GNOME) inside the container, exposes it
through noVNC on `:6080`, and optionally launches a Vaner Desktop
AppImage you mount in. Same standard Ubuntu display server that ships
to users — Xvfb is just a virtual framebuffer for the same Xorg stack.

Build:

```bash
docker build -f tests/integration/testbed/Dockerfile.gui \
             -t vaner-testbed-gui .
```

Run with the AppImage you want to drive (mount it read-only):

```bash
docker run --rm -it \
  -p 6080:6080 \
  -v /tmp/vaner-desktop-local-3.AppImage:/home/vaner/vaner-desktop.AppImage:ro \
  --shm-size=2g \
  vaner-testbed-gui
```

Open <http://localhost:6080/vnc.html?autoconnect=1> in a browser. The
GNOME session boots; the wizard window appears a few seconds later.
Log out / kill the AppImage from the noVNC session to exit.

Or via compose:

```bash
VANER_DESKTOP_APPIMAGE=/tmp/vaner-desktop-local-3.AppImage \
  docker compose -f tests/integration/testbed/compose.yml up gui
```

`xdotool` is installed inside the image, so future smoke scripts can
drive the wizard from a `docker exec` rather than human clicks.

Future GUI clients (Cursor, Zed, VS Code, Continue, Cline, Windsurf,
Roo, Claude Desktop) install on top of the same image.

## Build + run

```bash
# From the repo root:
docker build -f tests/integration/testbed/Dockerfile -t vaner-testbed .

# One-shot smoke:
docker run --rm vaner-testbed bash /opt/testbed/test-launch-clients.sh

# Interactive shell:
docker run --rm -it vaner-testbed
```

Or via compose:

```bash
docker compose -f tests/integration/testbed/compose.yml run --rm smoke
docker compose -f tests/integration/testbed/compose.yml run --rm testbed
```

## What's not in scope

- Networking against external MCP servers (the launcher only writes
  config; nothing connects).
- Backend models — Vaner's daemon isn't started.
- Real model traffic — the smoke is purely "do the right files
  appear in the right places".
