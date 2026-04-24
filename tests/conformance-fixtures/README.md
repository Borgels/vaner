# Conformance fixtures

Golden JSON samples of every daemon response shape any desktop client
consumes. Three independent consumers verify against these:

1. **Python** (`tests/test_conformance/test_fixtures_match.py`) — round-
   trips each fixture through the Pydantic models the daemon itself
   uses for serialization. Fails when a fixture diverges from the
   type contract.
2. **Rust crate** (`crates/vaner-contract/tests/conformance.rs`) —
   `include_str!()`s each fixture and decodes into the Rust types. The
   Linux Tauri app transitively depends on this suite.
3. **Swift macOS app** — a `ConformanceFixturesTests.swift` target in
   `Borgels/vaner-desktop` fetches the tarball of this directory
   from each Vaner release and decodes via its `Codable` models.

When the daemon changes a response shape, every consumer's conformance
test breaks until its side of the contract is updated. That's the
forcing function that keeps the three clients aligned.

## Files

| File                                 | Endpoint / surface                               |
|--------------------------------------|--------------------------------------------------|
| `predictions_active_sample.json`     | `GET /predictions/active` envelope (3 rows)      |
| `predictions_single_sample.json`     | `GET /predictions/{id}` (single row, no envelope)|
| `adopt_response_rich.json`           | `POST /predictions/{id}/adopt` with a full briefing + draft + WS8 alternatives/gaps/next_actions |
| `adopt_response_minimal.json`        | `POST /predictions/{id}/adopt` before any artifacts are attached (briefing + draft null) |
| `error_codes/adopt_not_found.json`   | 404 body from adopt — `{code: "not_found", ...}` |
| `error_codes/adopt_engine_unavailable.json` | 409 body from adopt                     |
| `error_codes/adopt_invalid_input.json` | 400 body from adopt                            |

Timestamps use fixed epoch values so the fixtures are byte-stable
across regenerations. Live responses include real `time.time()`
values; tests that compare against these fixtures must normalize the
timestamp fields or diff structurally.

## Regenerating

The fixtures are hand-authored against the daemon's documented schema,
not captured live. When adding a new one, mirror the shape the daemon
actually emits (look at `src/vaner/daemon/http.py` and
`src/vaner/mcp/contracts.py`), then run the Python conformance test
to confirm Pydantic accepts it.
