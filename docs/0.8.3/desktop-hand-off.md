# 0.8.3 WS4 — vaner-desktop hand-off

The macOS desktop app lives in a separate repo (`vaner-desktop/`) with
its own SwiftUI/AppKit codebase, build pipeline, and review cycle. The
Vaner repo's WS4 ships everything the desktop needs to integrate
without further engine changes; this note is the contract.

## Wire format

The desktop app talks to the daemon over loopback HTTP at
`127.0.0.1:8473` (the same `HTTPEngineClient` it already uses). All
five Deep-Run endpoints accept and return JSON in the canonical schema
defined by [src/vaner/cli/commands/deep_run.py](../../src/vaner/cli/commands/deep_run.py)
(`_session_to_dict` + `_summary_to_dict`):

| Method | Path                                  | Body / Query                                                                                                        | Response                                       |
|--------|---------------------------------------|---------------------------------------------------------------------------------------------------------------------|------------------------------------------------|
| POST   | `/deep-run/start`                     | `{ ends_at, preset?, focus?, horizon_bias?, locality?, cost_cap_usd?, metadata? }`                                  | `DeepRunSession` row                           |
| POST   | `/deep-run/stop`                      | `{ kill?: bool, reason?: string }`                                                                                  | `{ summary: DeepRunSummary \| null }`          |
| GET    | `/deep-run/status`                    | —                                                                                                                   | `{ session: DeepRunSession \| null }`          |
| GET    | `/deep-run/sessions?limit=N`          | —                                                                                                                   | `{ sessions: DeepRunSession[] }`               |
| GET    | `/deep-run/sessions/{id}`             | —                                                                                                                   | `DeepRunSession` (404 if not found)            |

The `vaner status` daemon endpoint (`GET /status`) already gains a
`deep_run` field of shape `{ active: bool, session: DeepRunSession | null }`
so the menu-bar indicator can render from one round-trip.

## SwiftUI scope (stretch — defer to a follow-up PR in vaner-desktop)

Three additions, all read/write the canonical record:

1. **Menu-bar quick action** in `Popover/PopoverRoot.swift` — a "Deep-Run
   tonight" button that opens a compact sheet (`DeepRunSheet.swift`) with:
   - Until-time picker (defaults to 07:00 next morning)
   - Preset segmented control (Conservative / Balanced / Aggressive)
   - Local-only toggle
   - Optional cost-cap field (defaults to $0)
2. **Active-session indicator** — menu-bar icon adopts a moon-glyph
   badge while a session is active; clicking reveals
   `DeepRunStatusPanel.swift` (preset, time remaining, cycles, the
   four maturation counters as separate values, "Stop" button).
3. **Preferences pane** addition in `Companion/PreferencesPane.swift`
   — defaults card (preset, cost cap, locality, "pause on user input").

The schema mirror lives in `vaner-desktop/State/DeepRunModels.swift`
(to be added). Suggested types are exact mirrors of
[ui/cockpit/src/types/deepRun.ts](../../ui/cockpit/src/types/deepRun.ts).

## Honest 4-counter discipline

Per spec §9.2 / §14.1, every surface that displays maturation activity
must show all four counters as separate values, never collapsed into a
single "matured" total:

- `matured_kept` — judge approved + persisted
- `matured_discarded` — judge rejected the new draft
- `matured_rolled_back` — kept then contradicted in probation
- `matured_failed` — exhausted per-prediction failure cap

The CLI / MCP / cockpit already enforce this. Desktop UI must follow.

## Notifications

When a session ends, the daemon emits a `deep_run_ended` event. Desktop
posts a macOS user notification: "Deep-Run finished. {kept} matured
kept, {cycles_run} cycles, ${spend_usd} spent." If
`cost_cap_exceeded` ever appears in the session's pause reasons, the
notification copy switches to surface that explicitly.

## Out of scope for 0.8.3

- Recurring schedule ("every weeknight 11pm–7am") — defer to 0.8.4 with
  a `vaner.schedules.*` MCP surface.
- Per-archetype preset auto-tuning — depends on archetype becoming
  first-class post-0.8.3.
