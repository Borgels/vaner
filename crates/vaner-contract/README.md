# vaner-contract

Cross-platform contract integration layer for Vaner desktop clients.

This crate is consumed by:

- The Linux Tauri app (`github.com/Borgels/vaner-linux`).
- The future Windows Tauri app (same repo, different bundle target).

The macOS SwiftUI app (`github.com/Borgels/vaner-desktop`) does **not** compile
against this crate. It runs the same JSON conformance fixtures (published
under `tests/conformance-fixtures/` in this monorepo) through its own
`Codable` models, so spec drift between Swift and Rust fails CI on
whichever side has fallen behind.

## What's inside

| Module      | Responsibility |
|-------------|----------------|
| `models`    | `PredictedPrompt`, `Resolution`, `EngineStatus` (names mirror the Swift models 1:1). |
| `enums`     | Unknown-tolerant string enums (`PredictionSource`, `Readiness`, etc.). |
| `errors`    | `EngineClientError` — identical case list to the Swift side. |
| `http`      | `EngineClient` async trait + `HttpEngineClient` (reqwest) impl. |
| `sse`       | `/events/stream?stages=predictions` consumer; multi-line `data:` accumulator. |
| `reducer`   | `VanerState` + pure `reduce` fn with the documented precedence chain. |
| `handoff`   | `AdoptHandoff::stash` + XDG / Application Support / LocalAppData path logic. |

## Feature flags

- `default = ["http", "sse"]`
- `http` — reqwest client + `EngineClient` trait.
- `sse` — event stream (requires `http`).
- `ts-rs` — emit TypeScript types for the SvelteKit frontend; run `cargo test --features ts-rs` to regenerate.

## Conformance

Run `cargo test` to exercise every layer. `tests/conformance.rs` consumes
the shared fixtures at `../../tests/conformance-fixtures/`; any shape
drift between the daemon and this crate surfaces as a test failure with
a regeneration hint.

## Not published to crates.io

Downstream apps pin this crate by git tag:

```toml
[dependencies]
vaner-contract = { git = "https://github.com/Borgels/Vaner.git", tag = "v0.8.3", package = "vaner-contract" }
```

Tags follow the daemon's semver. Major bumps mean breaking contract
changes; minor bumps are additive (new optional fields, new enum
variants). Rustc MSRV is tracked in the workspace `rust-version`.
