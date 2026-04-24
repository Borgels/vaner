//! TypeScript type exports for the SvelteKit frontend.
//!
//! Built only when the `ts-rs` feature is active. The models themselves
//! are annotated via `#[cfg_attr(feature = "ts-rs", derive(TS),
//! ts(export))]`; the `export_to` destination is controlled crate-wide
//! in this module's test, which is what `cargo test --features ts-rs`
//! actually runs to regenerate the files.
//!
//! The generated files live at a workspace-relative path so they're
//! easy for the `vaner-linux` repo to vendor (the Tauri app commits a
//! copy of what this produces).
//!
//! Regenerate with:
//!
//! ```sh
//! cargo test --features ts-rs --package vaner-contract
//! ```
//!
//! CI checks `git diff` is empty afterwards — if not, the Rust types
//! moved without a frontend update, and the PR must sync them.

#[cfg(test)]
mod regen {
    use crate::enums::{HypothesisType, PredictionSource, Readiness, Specificity};
    use crate::models::{
        EngineStatus, PredictedPrompt, PredictionArtifacts, PredictionRun, PredictionSpec,
        Provenance, Resolution, ResolutionAlternative, ResolutionEvidence, ScenarioCounts,
    };
    use crate::reducer::VanerState;
    use ts_rs::TS;

    /// `cargo test --features ts-rs regen_types` writes every `TS`-
    /// derived type to `bindings/*.ts` in the crate root. The frontend
    /// vendors these (copies to `vaner-linux/src/lib/contract/`) and
    /// CI enforces that the committed copy matches.
    #[test]
    fn regen_types() {
        // Invoking `T::export_all_to` would write to each type's
        // default location (under `crates/vaner-contract/bindings/`).
        // `derive(TS)` + `ts(export)` on each type handles this
        // declaratively; this test just forces that code path to run
        // and proves a representative type exports cleanly.
        PredictedPrompt::export_all().expect("export_all PredictedPrompt");
        PredictionSpec::export_all().expect("export_all PredictionSpec");
        PredictionRun::export_all().expect("export_all PredictionRun");
        PredictionArtifacts::export_all().expect("export_all PredictionArtifacts");
        Resolution::export_all().expect("export_all Resolution");
        ResolutionEvidence::export_all().expect("export_all ResolutionEvidence");
        ResolutionAlternative::export_all().expect("export_all ResolutionAlternative");
        Provenance::export_all().expect("export_all Provenance");
        EngineStatus::export_all().expect("export_all EngineStatus");
        ScenarioCounts::export_all().expect("export_all ScenarioCounts");
        PredictionSource::export_all().expect("export_all PredictionSource");
        HypothesisType::export_all().expect("export_all HypothesisType");
        Specificity::export_all().expect("export_all Specificity");
        Readiness::export_all().expect("export_all Readiness");
        VanerState::export_all().expect("export_all VanerState");
    }
}
