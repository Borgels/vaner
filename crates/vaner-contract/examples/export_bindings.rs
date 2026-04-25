//! Regenerate the TypeScript bindings under `crates/vaner-contract/bindings/`.
//!
//! 0.8.6 WS11 — companion to the existing `regen_types` test in
//! `src/ts.rs`. The test path is fine for CI, but a dedicated binary
//! gives downstream consumers (notably `vaner-desktop-linux`) a single
//! command they can run from anywhere in the workspace:
//!
//! ```sh
//! cargo run --example export_bindings --features ts-rs --package vaner-contract
//! ```
//!
//! The output directory is whatever `ts-rs` decides — by default it
//! lands at `crates/vaner-contract/bindings/*.ts`. The bindings dir is
//! gitignored at the workspace root; downstream apps copy or symlink
//! it into their own tree.
//!
//! When invoked WITHOUT the `ts-rs` feature flag, the binary prints a
//! reminder and exits 0 — this lets `cargo build --examples` succeed
//! in default-feature CI without spuriously regenerating files.

#[cfg(feature = "ts-rs")]
fn main() {
    use ts_rs::TS;

    use vaner_contract::enums::{
        EtaBucket, HypothesisType, PredictionSource, Readiness, Specificity,
    };
    use vaner_contract::models::{
        EngineStatus, PredictedPrompt, PredictionArtifacts, PredictionRun, PredictionSpec,
        Provenance, Resolution, ResolutionAlternative, ResolutionEvidence, ScenarioCounts,
    };
    use vaner_contract::reducer::VanerState;
    use vaner_contract::setup::{
        AppliedPolicy, BackgroundPosture, CloudPosture, ComputePosture, DeepRunDefaults,
        DetectedModel, HardwareProfile, HardwareTier, PolicyConfig, Priority, SelectionResult,
        SetupAnswers, SetupConfig, SetupQuestion, SetupQuestionOption, VanerPolicyBundle,
        WorkStyle,
    };

    // Each `export_all` call writes the type *and* its transitive
    // dependencies, deduplicated by ts-rs. We invoke once per
    // top-level type so any new transitive dependency lands without
    // editing this list.
    let exports: &[(&str, fn() -> Result<(), ts_rs::ExportError>)] = &[
        ("PredictedPrompt", PredictedPrompt::export_all),
        ("PredictionSpec", PredictionSpec::export_all),
        ("PredictionRun", PredictionRun::export_all),
        ("PredictionArtifacts", PredictionArtifacts::export_all),
        ("Resolution", Resolution::export_all),
        ("ResolutionEvidence", ResolutionEvidence::export_all),
        ("ResolutionAlternative", ResolutionAlternative::export_all),
        ("Provenance", Provenance::export_all),
        ("EngineStatus", EngineStatus::export_all),
        ("ScenarioCounts", ScenarioCounts::export_all),
        ("PredictionSource", PredictionSource::export_all),
        ("HypothesisType", HypothesisType::export_all),
        ("Specificity", Specificity::export_all),
        ("Readiness", Readiness::export_all),
        ("EtaBucket", EtaBucket::export_all),
        ("VanerState", VanerState::export_all),
        // 0.8.6 WS12a — setup-wizard contract types.
        ("WorkStyle", WorkStyle::export_all),
        ("Priority", Priority::export_all),
        ("ComputePosture", ComputePosture::export_all),
        ("CloudPosture", CloudPosture::export_all),
        ("BackgroundPosture", BackgroundPosture::export_all),
        ("HardwareTier", HardwareTier::export_all),
        ("SetupAnswers", SetupAnswers::export_all),
        ("VanerPolicyBundle", VanerPolicyBundle::export_all),
        ("DetectedModel", DetectedModel::export_all),
        ("HardwareProfile", HardwareProfile::export_all),
        ("SelectionResult", SelectionResult::export_all),
        ("AppliedPolicy", AppliedPolicy::export_all),
        ("SetupConfig", SetupConfig::export_all),
        ("PolicyConfig", PolicyConfig::export_all),
        ("DeepRunDefaults", DeepRunDefaults::export_all),
        ("SetupQuestion", SetupQuestion::export_all),
        ("SetupQuestionOption", SetupQuestionOption::export_all),
    ];

    let mut failures: Vec<(&str, ts_rs::ExportError)> = Vec::new();
    for (name, export) in exports {
        match export() {
            Ok(()) => println!("ok   {name}"),
            Err(err) => failures.push((name, err)),
        }
    }

    if !failures.is_empty() {
        eprintln!();
        eprintln!("ts-rs export failed for {} type(s):", failures.len());
        for (name, err) in &failures {
            eprintln!("  - {name}: {err}");
        }
        std::process::exit(1);
    }

    eprintln!();
    eprintln!(
        "Bindings written under crates/vaner-contract/bindings/ \
         (gitignored at workspace root)."
    );
    eprintln!("Downstream apps (e.g. vaner-desktop-linux) vendor by copy or symlink.");
}

#[cfg(not(feature = "ts-rs"))]
fn main() {
    eprintln!(
        "vaner-contract: example `export_bindings` requires --features ts-rs.\n\
         Re-run with:\n  cargo run --example export_bindings \
         --features ts-rs --package vaner-contract"
    );
}
