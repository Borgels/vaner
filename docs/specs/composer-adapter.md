# ComposerAdapter — Live Intent Capture contract (v0.8.7)

Vaner consumes explicit composer/prompt lifecycle events from supported
clients via a **ComposerAdapter**. Each event carries a
`DraftIntentSnapshot` plus a capability declaration, and lands on the
daemon's `POST /signals/composer` endpoint as a `SignalEvent` of kind
`composer_lifecycle`.

This document is the cross-language contract for non-Python adapter
authors. The pydantic models in
[`src/vaner/signals/composer/contract.py`](../../src/vaner/signals/composer/contract.py)
are the source of truth; the [JSON Schema artifact](composer-adapter.schema.json)
is generated from them by `vaner-composer-schema`.

## Privacy contract

- The draft text itself **never** crosses this boundary. Adapters send
  a `text_hash` (sha256 of the draft text) and a length, never the raw
  text or a preview.
- Local-only by default. Cloud processing of unsent draft text is **not**
  enabled in v0.8.7.
- No raw-draft persistence. The engine stores the snapshot, hash,
  lifecycle state, and counters; raw text never reaches storage.
- Per-client opt-in inherits the host's plugin/extension enable/disable
  surface in v0.8.7. First-class per-client opt-in scaffolding lands in
  v0.8.8.

## Capability levels

| Level | What the adapter can observe                                         | Honest `emits` |
|-------|----------------------------------------------------------------------|----------------|
| L0    | Submit-time only (the prompt as it leaves the composer)              | `("submitted",)` |
| L1    | Pre-submit draft lifecycle (debounced snapshots while user types)    | `("observing", "tentative", "stabilizing", "actionable", "submitted", "cleared", "abandoned")` |
| L2    | L1 + host-rendered affordance hook near the composer                 | same as L1 |
| L3    | L2 + host-native adopt flow for prepared packages                    | same as L1 |

Adapters MUST only emit lifecycle states they can actually observe.
A Level-0 adapter that fabricates `actionable` corrupts the engine's
scoring downstream. The daemon validates each event against the
adapter's declared `capabilities.emits` on ingest.

## Capability matrix (existing surfaces)

| Surface                     | Path / hook mechanism                                  | Level |
|-----------------------------|--------------------------------------------------------|-------|
| Claude Code (plugin)        | `UserPromptSubmit` hook (v0.8.7 WS7 MVP)               | L0    |
| Cursor                      | MCP tools only (no composer hook)                      | —     |
| Claude Desktop              | MCP tools only                                         | —     |
| VS Code (Copilot Chat MCP)  | MCP tools only                                         | —     |
| Codex CLI                   | MCP tools only                                         | —     |
| Windsurf                    | MCP tools only                                         | —     |
| Zed                         | MCP `context_servers` only                             | —     |
| Continue                    | MCP tools only                                         | —     |
| Cline                       | MCP tools only                                         | —     |
| Roo Code                    | MCP tools only                                         | —     |
| Vaner VS Code extension     | Cockpit/webview consuming daemon SSE                   | excluded |

The Vaner VS Code extension is **explicitly not** an adapter source.
Vaner does not own a composer; doing so would violate the "Vaner is
not a client" thesis. The extension surfaces prepared-package state
from the daemon — it does not observe a user composer.

Other clients enter the matrix only when an explicit, documented
hook/plugin/extension mechanism exists in the host. Generic input
capture, accessibility APIs, terminal scraping, and unsupported DOM
injection are not acceptable adapter mechanisms.

## Wire format

Adapters POST to the loopback daemon endpoint:

```
POST http://127.0.0.1:<daemon_port>/signals/composer
Content-Type: application/json

<DraftIntentSnapshot>
```

The endpoint envelopes the snapshot into a
`SignalEvent(kind="composer_lifecycle", payload=<snapshot>)` and calls
`engine.observe()`. The response carries a `composer_event_id` (UUID)
that the adapter MAY surface to the host so subsequent adoptions can
attribute back to the originating event.

A snapshot looks like (L0 example):

```json
{
  "session_id": "claude-code-session-abc123",
  "snapshot_id": "01HX...",
  "timestamp": "2026-04-25T20:14:45Z",
  "lifecycle_state": "submitted",
  "text_hash": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
  "length_chars": 142,
  "capabilities": {
    "level": "L0",
    "emits": ["submitted"],
    "host_app": "claude-code",
    "host_kind": "ai_chat_client"
  },
  "workspace_id": "<workspace-id>",
  "field_role": "agent_prompt"
}
```

## Authoring an adapter

1. Pick a host that exposes an explicit composer/prompt lifecycle hook.
   If the host has no such mechanism, do not invent one — wait for the
   host to ship one.
2. Decide the honest capability level. If the hook only fires at submit
   time, declare `level: "L0"` and `emits: ["submitted"]`.
3. Compute `text_hash = sha256(draft_text)`. Never log, store, or
   transmit the raw draft text.
4. Build a `DraftIntentSnapshot` per the schema and POST it to
   `http://127.0.0.1:<daemon_port>/signals/composer` with a short
   timeout (≤ 1 s recommended). On failure, fail silent — never block
   the user's prompt.
5. If the adapter is per-prompt (L0), one POST per submit. If the
   adapter is L1, debounce snapshot emission (≥ 250 ms between
   meaningful changes) and only emit when the lifecycle state would
   actually change.

## Regenerating the JSON Schema

```bash
vaner-composer-schema      # writes docs/specs/composer-adapter.schema.json
git diff --exit-code docs/specs/composer-adapter.schema.json
```

CI runs the regeneration step and fails if the artifact drifts from
what the pydantic models would produce.
