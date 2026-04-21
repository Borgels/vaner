# Cockpit dogfood report

Branch: `cockpit-hardening-wiring`
Tested on: Linux 6.17 / RTX 5090 (32 GB) / Python 3.12 / Ollama `qwen2.5-coder:7b`
Scratch workspace: `/tmp/vaner-cockpit-demo`
Ports used: daemon `:8473`, MCP SSE `:8482`, MCP stdio sidecar `:8484`

## TL;DR

| Surface | State before fixes | State after fixes |
| --- | --- | --- |
| Daemon cockpit (`:8473`) | Loaded, API wired, most interactions work | Unchanged |
| MCP SSE cockpit (`:8482/cockpit/`) | Blank white page; all API calls 404 | Renders at `:8482/`, API wired |
| MCP stdio sidecar (`:8484`) | Process crashed on startup | Serves cockpit, stays up |
| MCP JSON-RPC tools (`list_scenarios` etc.) | 100 % crashed on every call | Work end-to-end |
| Cockpit → `.vaner/config.toml` writeback | Works (backend/compute/context/MCP) | Unchanged |
| Skill nudge, scenario pin, feedback | Work server-side | Unchanged |

Two patches were applied during this session to unblock dogfooding and are already in the working tree (see "Applied during session" below). The remaining items are queued as a fix-and-polish plan.

## Environment / starting conditions

- `vaner` was resolving to a stale `uv tool install` that predated the cockpit factory: its `/cockpit/bootstrap.json` 404'd.
- Three parallel `vaner` installs coexisted: pipx (openclaw proxy, untouched), uv-tool (global `vaner`), editable `pip install -e .` in this repo.
- `~/.cursor/mcp.json` had `"mcpServers": {}`.
- Ollama `:11434` ready with `qwen2.5-coder:7b`.

Upgrade path used: `uv tool install --reinstall --python 3.12 '.[mcp]'` from [/home/abo/repos/Vaner](.) after rebuilding the SPA with `npm run build` in [ui/cockpit](ui/cockpit).

## Findings

### Defects

#### D1. MCP server crashes on every client call (both transports) — CRITICAL, patched
`server.get_capabilities(notification_options=None, ...)` raises `AttributeError: 'NoneType' object has no attribute 'tools_changed'` in the installed `mcp` SDK. Stdio mode died before the first message; SSE mode died on the first `initialize` request, leaving clients hanging. Every MCP client (Claude Desktop, Cursor, our `ClientSession`) was effectively unable to talk to Vaner.
- Repro: `vaner mcp --path /tmp/x` (stdio) or any MCP initialize over `:8482/sse`.
- Cause: `notification_options=None` passed where SDK unconditionally dereferences `.tools_changed`.
- Fix applied in [src/vaner/mcp/server.py](src/vaner/mcp/server.py) — import `NotificationOptions` and pass `NotificationOptions()` in both `run_stdio` and `run_sse`.
- Follow-up: add a regression test — spin up `run_sse` in a fixture and run `ClientSession.initialize()` against it. Current `tests/test_mcp/test_mcp_cockpit.py` only checks the cockpit HTTP app, not the protocol handshake.

#### D2. MCP SSE cockpit UI is a blank page — CRITICAL, patched
SPA's built `index.html` references `/assets/index-…js` with an absolute path. `run_sse` mounted the cockpit under `/cockpit/`, so `/assets/…` 404'd. `/cockpit/bootstrap.json` also 404'd (actual path was `/cockpit/cockpit/bootstrap.json`). Every API call in the client (`/status`, `/scenarios`, `/skills`) 404'd.
- Repro: `vaner mcp --transport sse --port 8482 --path .` → open `http://127.0.0.1:8482/cockpit/`.
- Fix applied in [src/vaner/mcp/server.py](src/vaner/mcp/server.py) — mount `build_cockpit(repo_root)` at `/` (not `/cockpit/`) when `cockpit_enabled`. The SSE transport endpoints (`/sse`, `/messages/`) do not collide because the cockpit factory only owns other paths.
- Follow-up: extend [tests/test_mcp/test_mcp_cockpit.py](tests/test_mcp/test_mcp_cockpit.py) with a Starlette test that asserts `GET /` returns the SPA and `GET /assets/*` returns 200 under the SSE wrapper. Also delete or repurpose `redirect_to_cockpit` in docs.

#### D3. `vaner query` crashes on the default install — HIGH
`ModuleNotFoundError: sentence_transformers` inside [src/vaner/clients/embeddings.py](src/vaner/clients/embeddings.py). The README's "Run it" section (`vaner query "..."`) fails immediately for anyone who installed with the one-liner.
- Same hit from `expand_scenario` MCP tool → `"ERROR: No module named 'sentence_transformers'"`.
- `[project.optional-dependencies]` in [pyproject.toml](pyproject.toml) splits embeddings into a separate `[embeddings]` extra that neither `install.sh` nor `vaner[mcp]` pulls in.
- Options:
  1. Roll embeddings into the default dep set (adds torch weight ≈1 GB — probably a no-go for OpenSSF/install size).
  2. Wrap the embed path with `try/except ImportError` and fall back to lexical matching with a helpful log line.
  3. Teach `scripts/install.sh` to offer `VANER_EMBEDDINGS=1` → append `[mcp,embeddings]` and document it in `README.md`.
- Recommendation: option 2 as the core fix + option 3 as a post-install hint. The current UX ("traceback then exit 1" on the very first documented command) is worst-case.

#### D4. GPU probe can't see CUDA devices — HIGH
`/compute/devices` returns just `[{id:"cpu"}]` with `warning: "No module named 'torch'"` even though the box has an RTX 5090 with 32 GB VRAM. The device dropdown in the Settings drawer is therefore useless for GPU selection (the user's original complaint from the prior session).
- Root cause: the probe is implemented with `torch.cuda.device_count()` which requires the optional `embeddings` extra.
- Fix options (can be combined):
  1. Use `nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader` as a fallback when torch is missing. Works on this box.
  2. On Linux, fall back to scanning `/proc/driver/nvidia/gpus/*/information`.
  3. Add `amd` (ROCm) probe via `rocm-smi` for AMD boxes.
- Surface the degraded state in the UI too: currently the Settings drawer just shows a red "No module named 'torch'" string with no actionable link. Replace it with a one-liner like "Install `vaner[embeddings]` (or verify NVIDIA drivers) to enable GPU selection" plus a "docs" link.

#### D5. Backend preset switch doesn't fully resync UI form state — MEDIUM
After `POST /backend` succeeds, the server correctly rewrites `[backend]` (e.g., `lmstudio` clears `model` and `api_key_env`), but the cockpit's Settings drawer continues to display the stale local input values (`Model: "qwen2.5-coder:7b"`, `API key env: "OPENAI_API_KEY"`) until a manual page reload. Same for the Preset combobox showing lag (`value: lmstudio` right after selecting `ollama`).
- Cause: `SettingsDrawer` form state is seeded once from the initial `/status` fetch and only the field(s) touched by `onSaveBackend` are re-hydrated. In [ui/cockpit/src/components/chrome.tsx](ui/cockpit/src/components/chrome.tsx) the `backend` local state should be replaced wholesale with the server's response (`response.backend`) after every `POST /backend`.
- Also: when picking a preset, the UI should apply the preset's defaults to the form *before* POSTing so the user can preview/override.

#### D6. Pinning a scenario is a no-op for the "Pinned Context" rail — MEDIUM
Clicking Pin in the Inspector toggles button label to "Unpin" and sets `scenarios.pinned=1`, but the left-rail "Pinned Context" list is driven by `/pinned-facts` (a separate SQLite table for free-text notes). So pinning a scenario never surfaces it in the rail. Users will assume the feature is broken.
- Repro: click Pin on any scenario in the inspector → Pinned Context stays "No pinned facts".
- Fix options:
  1. Have `/pinned-facts` include a synthesized fact row for each `pinned=1` scenario (e.g., `{kind: "scenario", id, title, note: scenario.prepared_context}`).
  2. Add a second section in the left rail: "Pinned scenarios" driven by `/scenarios?pinned=true`.
  3. Rename the button "Pin to context" → "Mark as always-include" and document the difference from the explicit fact notes.
- Recommendation: option 1 (lowest UI churn, matches the user's mental model).

#### D7. Cockpit UI does not auto-refresh on MCP tool calls — MEDIUM
`report_outcome` via the MCP tool correctly persists `last_outcome=partial`, but the Inspector continues to show the previous state until the user hits refresh. The `/events/stream` SSE feed only publishes ponder-loop events, not scenario mutations.
- Fix: publish to the existing `asyncio.Queue` in `ScenarioStore` whenever `record_outcome`, `set_pinned`, or `expand_scenario` mutates a row. The UI already subscribes to `/events/stream` and `/scenarios/stream`.
- Secondary: surface MCP tool invocations themselves as events ("MCP: report_outcome scn_…"), so the Event Stream panel becomes the single pane of glass for "who did what".

#### D8. `vaner init` reports `device=cpu gpu_count=0 vram_gb=0` on a CUDA-capable host — MEDIUM
Same root cause as D4. The installer/init path also only uses torch for detection, so the user sees a misleading "Hardware profile: device=cpu" at first run even though they have an RTX 5090.

#### D9. MCP tool parameter name drift — LOW
Tool schemas expose `{id}` but the CLI + cockpit use `scenario_id`. Tools fail with `Input validation error: 'id' is a required property` when called with `scenario_id`. Pick one name, update [src/vaner/mcp/server.py](src/vaner/mcp/server.py) schema + all call sites, and document.

#### D10. `vaner --version` does not exist — TRIVIAL
`vaner --version` returns `No such option: --version Did you mean --verbose?`. Typer has a first-class version callback; adding it is ~3 lines.

### UX gaps

#### U1. FrontierGraph layout is a stacked column for the demo dataset
All 5 scenarios render in a single vertical column with identical X-coordinates, plus the heading glyph renders as "FRONTIER PS0E3AARIO GRAPH" (overlapping labels). The `kind=change` cluster has no horizontal spread. Either:
- Use a force-directed layout seeded by (kind, depth, score).
- Scale node X by score, Y by freshness bucket, so the graph has shape even with few scenarios.
- Fix the header label collision (see `FrontierGraph` title + subtitle overlap).

#### U2. Evidence panel shows raw `diff --git` snippets
The daemon emits evidence notes like `"Snippet: diff --git a/auth.py …"` — the cockpit renders them verbatim, horizontal-scroll, monospace. It's unreadable and defeats the purpose of the Evidence panel. Either:
- Strip the diff machinery in `_evidence_payload` and render a cleaner code excerpt.
- Switch to a three-mode toggle: "Diff / File snippet / Raw".

#### U3. Score breakdown is always empty
Every scenario shows "No score components reported yet. The daemon only publishes a breakdown after it runs the next scoring pass." on first render. Given this is shown for every scenario immediately after a fresh `daemon start --once`, the copy needs rework (or the scoring pass needs to write components on first run).

#### U4. `vaner init` writes a `.cursor/mcp.json` inside the project and an entry for Claude Desktop automatically
This is friendly but can surprise users who opened init from a repo that already has its own `.cursor/mcp.json` or who don't use Claude Desktop. Consider an opt-in flag (`--mcp-clients cursor,claude,codex`) and `--dry-run`.

#### U5. "Pinned Context" empty-state string mentions "Pin scenarios" but pinning does nothing (see D6)
Tie this to D6's fix; once pinning a scenario surfaces in the rail, the copy is fine.

#### U6. Viewport-minimum rendering
At ~340 px wide (the first screenshot before resize), several labels overlap and the FrontierGraph canvas becomes unreadable. Add a min-width guard or a condensed mobile layout — the IDE browser's default viewport is narrower than you might assume.

### Optimizations

#### O1. The installer installs `[mcp]` but leaves the user with a half-working CLI (D3)
Either roll the embed path into `[mcp]` behind an import-time fallback, or bundle a sensible default of `sentence-transformers` at a size users accept. Right now `vaner query` in the Quickstart is a broken-first-impression.

#### O2. Event stream is under-utilized
`/events/stream` only sees the ponder loop. Extending to scenario mutations (D7) makes the cockpit a real-time control surface.

#### O3. SPA is 200 KB gzipped, 60 KB brotli-ish — acceptable, but low-hanging wins exist
- `ui/cockpit/src/App.tsx` imports every component up-front. Code-splitting the Settings drawer alone would shave ~8 KB off initial JS.
- `@fontsource/*` is bundled for 3 font families × 3 weights × 2 scripts = ~600 KB of woff/woff2. A `.env`-driven subset or drop to system fonts by default would cut cockpit size by >75 %.

#### O4. Scenario graph recomputes layout from scratch every render
Memoize the force layout keyed on `scenarios.map(s => s.id + s.score).join('|')` — the current `useMemo` dep set triggers on every heartbeat.

#### O5. `/status` is polled every heartbeat
The cockpit refetches `/status` every few seconds even when nothing changed. Combine with O2 and replace with an SSE-driven delta-only update from the backend.

#### O6. Repeated `sqlite3` connections in `ScenarioStore._add_column_if_missing`
Each `ALTER TABLE` opens a fresh `aiosqlite.connect`. Consolidate migrations into a single transaction in `initialize()` — saves ~30 ms on first boot.

## Applied during session

| File | Change |
| --- | --- |
| [src/vaner/mcp/server.py](src/vaner/mcp/server.py) | Import `NotificationOptions`, pass `NotificationOptions()` in both `run_stdio` and `run_sse` (fixes D1). |
| [src/vaner/mcp/server.py](src/vaner/mcp/server.py) | SSE Starlette wrapper now mounts `build_cockpit(...)` at `/` instead of `/cockpit/` and drops the redirect; fixes D2. Docstring updated. |

### Pipeline view refactor (D7 + U1 + U2-adjacent + U5)

Landed on branch `cockpit-pipeline-view` (see the "Cockpit live pipeline view"
section of the README):

- **Backend** introduces `src/vaner/events/bus.py` — a unified `EventBus` with a
  structured `VanerEvent` dataclass (`stage`, `kind`, `payload`, `scn`, `path`,
  `cycle_id`) plus a `cycle_scope` context var. The daemon runner, LLM helpers,
  proxy chat completion, and `ScenarioStore._publish` all emit through it.
  `/events/stream` now reads from the bus and supports a `?stages=` filter.
  Closes **D7** (scenario mutations are broadcast live) and **O2** (event stream
  covers every stage of the pipeline, not just the ponder loop).
- **Frontend** replaces `FrontierGraph` with a two-layer view:
  - `PipelineCanvas` renders a six-lane ribbon (Signals → Targets → Model →
    Artefacts → Scenarios → Decisions) with live read-outs per lane and
    particle flow between them. Closes **U1** — even a small frontier now
    reads as a connected pipeline rather than a vertical stack.
  - `ScenarioCluster` uses a kind-bucketed force-directed layout and adds
    **shared-path Jaccard edges** between scenarios so scenarios without an
    explicit parent still form a visible constellation when they touch the
    same files. This is the "edge semantics that brings users value" — the
    graph now mirrors the real overlap structure of the work.
- `SystemVitals` (left-rail header) surfaces mode, cycle, model busy-state,
  last LLM latency, model id, total cycles, artefacts written, scenarios, and
  error count — all derived from the event bus (no polling). Together with the
  pipeline ribbon this makes "is the model working in the background?" a
  single-glance answer.
- `EventStreamPanel` replaces the old `StreamPanel`: colour-coded by stage,
  per-stage filter chips, collapsed heartbeats, and a header LLM spinner
  counting in-flight requests.
- **U5** ("Pin scenarios does nothing") remains tracked as D6; the pipeline
  refactor doesn't change pinning semantics but the new cluster does visibly
  flag pinned scenarios via the amber dot and keeps them highlighted in the
  shared-path constellation.

The backend event bus is covered by `tests/test_events/test_bus.py`,
`tests/test_daemon/test_runner_events.py`,
`tests/test_daemon/test_generator_events.py`,
`tests/test_router/test_proxy_events.py`, and the updated
`tests/test_daemon/test_http.py` (stage filter + structured envelope). The new
frontend components are covered by `SystemVitals.test.tsx`,
`EventStreamPanel.test.tsx`, `PipelineCanvas.test.tsx`, and
`ScenarioCluster.test.ts` (Jaccard + layout + force tick).

## Recommended priority order

1. D1 (critical, already patched — add regression test, commit).
2. D2 (critical, already patched — add regression test, commit).
3. D3 + O1 (first-impression breakage on `vaner query`).
4. D4 / D8 (GPU probe falls back to `nvidia-smi`).
5. D6 (pin → pinned facts).
6. D7 (live UI updates on MCP tool calls).
7. D5 (form hydration after preset change).
8. U1 / U2 / U3 (cockpit polish).
9. D9 / D10 (naming + `--version`).
10. O3 – O6 (perf tuning).

Open a follow-up branch `cockpit-dogfood-fixes` off `cockpit-hardening-wiring` to land items 1-7 as a tight PR; park 8-10 for a separate polish PR.
