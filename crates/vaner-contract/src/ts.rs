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
    use crate::enums::{EtaBucket, HypothesisType, PredictionSource, Readiness, Specificity};
    use crate::models::{
        EngineStatus, PredictedPrompt, PredictionArtifacts, PredictionRun, PredictionSpec,
        Provenance, Resolution, ResolutionAlternative, ResolutionEvidence, ScenarioCounts,
    };
    use crate::reducer::VanerState;
    use crate::setup::{
        AppliedPolicy, BackgroundPosture, CloudPosture, ComputePosture, DeepRunDefaults,
        DetectedModel, HardwareProfile, HardwareTier, PolicyConfig, Priority, SelectionResult,
        SetupAnswers, SetupConfig, SetupQuestion, SetupQuestionOption, VanerPolicyBundle,
        WorkStyle,
    };
    use ts_rs::TS;

    /// `cargo test --features ts-rs regen_types` writes every `TS`-
    /// derived type to `bindings/*.ts` in the crate root. The frontend
    /// vendors these (copies to `vaner-linux/src/lib/contract/`) and
    /// CI enforces that the committed copy matches.
    ///
    /// 0.8.6 WS11: kept in lockstep with the `examples/export_bindings.rs`
    /// list — the example binary is the equivalent path for downstream
    /// consumers who want a single command rather than the test harness.
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
        EtaBucket::export_all().expect("export_all EtaBucket");
        VanerState::export_all().expect("export_all VanerState");

        // 0.8.6 WS12a — setup-wizard contract types. Kept in lockstep
        // with the `examples/export_bindings.rs` exports list so CI
        // catches drift in either entry point.
        WorkStyle::export_all().expect("export_all WorkStyle");
        Priority::export_all().expect("export_all Priority");
        ComputePosture::export_all().expect("export_all ComputePosture");
        CloudPosture::export_all().expect("export_all CloudPosture");
        BackgroundPosture::export_all().expect("export_all BackgroundPosture");
        HardwareTier::export_all().expect("export_all HardwareTier");
        SetupAnswers::export_all().expect("export_all SetupAnswers");
        VanerPolicyBundle::export_all().expect("export_all VanerPolicyBundle");
        DetectedModel::export_all().expect("export_all DetectedModel");
        HardwareProfile::export_all().expect("export_all HardwareProfile");
        SelectionResult::export_all().expect("export_all SelectionResult");
        AppliedPolicy::export_all().expect("export_all AppliedPolicy");
        SetupConfig::export_all().expect("export_all SetupConfig");
        PolicyConfig::export_all().expect("export_all PolicyConfig");
        DeepRunDefaults::export_all().expect("export_all DeepRunDefaults");
        SetupQuestion::export_all().expect("export_all SetupQuestion");
        SetupQuestionOption::export_all().expect("export_all SetupQuestionOption");
    }
}
