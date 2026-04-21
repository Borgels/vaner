# Vaner Cockpit

The Vaner cockpit is a single bundled React/Vite SPA served from the same
factory by every Vaner surface so there is only one UI to learn and
maintain:

- `vaner daemon serve-http` serves it at `http://127.0.0.1:8473/` in
  `mode = "daemon"`.
- `vaner proxy` serves it at `http://127.0.0.1:8472/` (or whichever port
  you pass) in `mode = "proxy"`, co-existing with
  `/v1/chat/completions`.
- `vaner mcp` serves it alongside the MCP transport:
  - `--transport stdio` spawns an HTTP sidecar on `--cockpit-host` /
    `--cockpit-port` (default `127.0.0.1:8473`).
  - `--transport sse` mounts the cockpit under `/cockpit/` on the same
    port as the SSE endpoint, with `/` redirecting to `/cockpit/`.
  - Pass `--no-cockpit` to fall back to the legacy headless behaviour.

All three modes share the same `build_cockpit_app` factory in
`src/vaner/ui/server.py`. The SPA adapts per mode at runtime using the
payload returned by `/cockpit/bootstrap.json`.

## Modes

- `daemon`: scenario frontier graph, inspector, skills (with persistent
  nudge buttons), pinned context, and live lifecycle events from
  `/events/stream`.
- `proxy`: decisions timeline, selected decision JSON, impact summary,
  gateway toggle, and live proxy decision events from
  `/decisions/stream`.
- `mcp`: same scenario view as `daemon`, exposed next to the MCP
  transport so operators can watch which scenarios the agent is
  consuming.

## Unified control plane

The cockpit's Settings drawer (opened with `Ctrl/Cmd+,`) is wired to
real APIs served from the factory:

| Section  | Endpoint(s)                                                      |
|----------|-------------------------------------------------------------------|
| Backend  | `GET /backend/presets`, `POST /backend` (preset + overrides)     |
| Compute  | `GET /compute/devices`, `POST /compute` (device + fraction fields) |
| Context  | `POST /context` (`limits.max_context_tokens`)                     |
| MCP      | `POST /mcp` (transport, host, port)                               |
| Appearance | Client-only cockpit preferences (accent, density, reduce-motion) |

Each POST persists the change back to `.vaner/config.toml` via the
`vaner config set` helpers, so a restart of the daemon / proxy / MCP
server picks up the same values.

## Bundle drift banner

`/cockpit/bootstrap.json` now exposes a `cockpit_sha` computed from the
running server's `index.html`. The SPA compares it to the build-time
constant `VITE_COCKPIT_SHA` and renders a "Cockpit bundle out of date"
banner when the two differ, which is a clear sign that the Vaner process
was started before `make ui-build` / `npm run build --prefix
ui/cockpit` produced the new bundle. Reload the tab after restarting
the backend to dismiss it.

## Dev workflow

```bash
make ui-install   # once
make dev          # build SPA + run daemon cockpit on :8473
# or
make dev-proxy    # build SPA + run proxy cockpit on :8472
# or
make dev-mcp      # build SPA + run MCP stdio + cockpit sidecar on :8473
```

For hot-reload development against an already-running backend, run the
Vite dev server and hit `http://127.0.0.1:5173/`:

```bash
make ui-dev
```

For a production bundle only:

```bash
make ui-build
```

That writes assets into `src/vaner/daemon/cockpit_assets/dist/`, which
is what the Python packages ship.

## Verification

Frontend:

```bash
cd ui/cockpit
npm run lint
npm run test
npm run build
```

Backend:

```bash
pytest tests/test_daemon/test_http.py tests/test_router/test_proxy_app.py tests/test_mcp/test_mcp_cockpit.py
```

## Keyboard shortcuts

- `Ctrl/Cmd+K`: open the command palette
- `Ctrl/Cmd+,`: open settings
- `Esc`: close overlays or clear selection focus
- `Left` / `Right` / `Up` / `Down`: move between daemon scenarios
- `U`: mark selected daemon scenario useful
- `P`: mark selected daemon scenario partial
- `X`: mark selected daemon scenario irrelevant
- `.`: pin or unpin the selected daemon scenario
