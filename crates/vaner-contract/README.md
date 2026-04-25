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

## TypeScript bindings (Linux desktop consumption)

`vaner-desktop-linux` (the SvelteKit/Tauri app) consumes these types
through `ts-rs`-generated TypeScript declarations. The bindings dir is
**gitignored** at the workspace root — every consumer regenerates
locally so the source of truth stays in Rust.

### Regenerate

Either of these commands writes `bindings/*.ts` under
`crates/vaner-contract/`:

```sh
# Test-driven (matches what CI runs in `.github/workflows/rust.yml`):
cargo test --features ts-rs --package vaner-contract regen_types

# Or via the dedicated example binary (one command, no test harness):
cargo run --example export_bindings --features ts-rs --package vaner-contract
```

Both paths emit the same files; pick whichever suits your tooling.

### Consume from `vaner-desktop-linux`

The Linux desktop's preferred pattern is to copy or symlink
`crates/vaner-contract/bindings/` into its own `src/lib/contract/`
tree at build time. A typical Tauri pre-build script:

```sh
# In vaner-desktop-linux's package.json `scripts.predev` /
# `scripts.prebuild`:
cargo run --example export_bindings --features ts-rs \
  --manifest-path ../Vaner/Cargo.toml --package vaner-contract
rsync -a ../Vaner/crates/vaner-contract/bindings/ src/lib/contract/
```

Don't commit the copy; treat it as a generated artefact. The CI step
`test (ts-rs feature)` in `rust.yml` regenerates on every push and
fails the build if any annotated type can't export, so the Linux side
gets a clean cross-repo signal when a contract change lands.

### macOS does NOT consume these bindings

`vaner-desktop-macos` (Swift) compiles its own `Codable` mirrors and
runs the same conformance fixtures from `tests/conformance-fixtures/`.
The bindings dir is irrelevant to the macOS build — Linux-desktop-only.

### When new public types are added

Any new public struct or enum that needs a TypeScript mirror must
carry both:

```rust
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
```

…and be added to the `export_all` list inside `examples/export_bindings.rs`
(plus the same list in `src/ts.rs`'s `regen_types` test). The test
path is what CI exercises; the example binary mirrors it for ad-hoc
local runs.

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
