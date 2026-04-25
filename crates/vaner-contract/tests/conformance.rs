//! Conformance tests against the shared JSON fixtures at
//! `tests/conformance-fixtures/` (two levels up from this crate).
//!
//! The fixtures are the cross-language contract's single source of
//! truth: Python, Rust, and Swift each decode the same bytes. If the
//! daemon's shape changes and the fixtures are regenerated, this test
//! breaks whenever the Rust types haven't been updated in lock-step.
//!
//! These are integration tests (not unit tests inside a module) so
//! they exercise the public crate API the way a downstream consumer
//! (vaner-linux) would.

use serde::Deserialize;
use vaner_contract::{
    EtaBucket, HypothesisType, PredictedPrompt, PredictionSource, Readiness, Resolution,
    Specificity,
};

#[derive(Deserialize)]
struct PredictionsEnvelope {
    predictions: Vec<PredictedPrompt>,
}

#[derive(Deserialize, Debug)]
struct ErrorBody {
    code: String,
    message: String,
}

fn load(name: &str) -> String {
    let path = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(std::path::Path::parent)
        .expect("crate dir has two parents")
        .join("tests/conformance-fixtures")
        .join(name);
    std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("reading fixture {}: {e}", path.display()))
}

#[test]
fn predictions_active_envelope_decodes() {
    let body = load("predictions_active_sample.json");
    let envelope: PredictionsEnvelope = serde_json::from_str(&body).expect("envelope must decode");
    assert_eq!(envelope.predictions.len(), 3);

    let ready = &envelope.predictions[0];
    assert_eq!(ready.id, "pred-ready-0001");
    assert_eq!(ready.spec.source, PredictionSource::Arc);
    assert_eq!(ready.spec.hypothesis_type, HypothesisType::LikelyNext);
    assert_eq!(ready.spec.specificity, Specificity::Concrete);
    assert_eq!(ready.run.readiness, Readiness::Ready);
    assert!(ready.artifacts.has_draft);
    assert!(ready.artifacts.has_briefing);
    assert_eq!(ready.readiness_label.as_deref(), Some("Ready"));
    assert_eq!(ready.eta_bucket, Some(EtaBucket::ReadyNow));
    assert_eq!(ready.eta_bucket_label.as_deref(), Some("Ready now"));
    assert_eq!(ready.adoptable, Some(true));
    assert_eq!(ready.rank, Some(1));
    assert_eq!(
        ready.ui_summary.as_deref(),
        Some("Ready test draft for webhook signing")
    );
    assert_eq!(ready.suppression_reason, None);
    assert_eq!(ready.source_label.as_deref(), Some("Arc"));

    let goal = &envelope.predictions[1];
    assert_eq!(goal.spec.source, PredictionSource::Goal);
    assert_eq!(goal.spec.anchor.as_deref(), Some("JWT migration"));
    assert_eq!(goal.run.readiness, Readiness::Drafting);
    assert_eq!(goal.eta_bucket, Some(EtaBucket::Under20s));
    // En-dash matches the daemon's `_ETA_BUCKET_LABELS` source of truth in
    // `vaner.intent.readiness`; the spec (3b.md) calls for a typographic
    // range dash. Hyphen-minus would silently break the wire-shape match.
    assert_eq!(goal.eta_bucket_label.as_deref(), Some("~10–20s"));
    assert_eq!(goal.adoptable, Some(true));
    assert_eq!(goal.rank, Some(2));

    let queued = &envelope.predictions[2];
    assert_eq!(queued.spec.source, PredictionSource::Macro);
    assert_eq!(queued.spec.hypothesis_type, HypothesisType::LongTail);
    assert_eq!(queued.spec.specificity, Specificity::Category);
    assert_eq!(queued.run.readiness, Readiness::Queued);
    assert!(!queued.artifacts.has_draft);
    assert_eq!(queued.spec.description, None);
    assert_eq!(queued.eta_bucket, Some(EtaBucket::Maturing));
    assert_eq!(queued.adoptable, Some(false));
    assert_eq!(queued.suppression_reason.as_deref(), Some("low_confidence"));
}

#[test]
fn predictions_single_decodes() {
    let body = load("predictions_single_sample.json");
    let prediction: PredictedPrompt =
        serde_json::from_str(&body).expect("single prediction must decode");
    assert_eq!(prediction.id, "pred-ready-0001");
    assert_eq!(prediction.run.readiness, Readiness::Ready);
    assert_eq!(prediction.eta_bucket, Some(EtaBucket::ReadyNow));
    assert_eq!(prediction.rank, Some(1));
    assert_eq!(prediction.adoptable, Some(true));
}

#[test]
fn adopt_rich_response_decodes_including_ws8_fields() {
    let body = load("adopt_response_rich.json");
    let resolution: Resolution = serde_json::from_str(&body).expect("rich Resolution must decode");

    assert_eq!(resolution.intent, "Write the next test for webhook signing");
    assert_eq!(resolution.resolution_id, "adopt-pred-ready-0001");
    assert_eq!(
        resolution.adopted_from_prediction_id.as_deref(),
        Some("pred-ready-0001")
    );
    assert_eq!(resolution.provenance.mode, "predictive_hit");
    assert!(resolution.prepared_briefing.as_deref().is_some());
    assert!(resolution.predicted_response.as_deref().is_some());

    // WS8 additive fields (0.8.0) — populated only on the rich sample.
    assert_eq!(resolution.alternatives_considered.len(), 1);
    assert_eq!(
        resolution.alternatives_considered[0].source,
        "scn_webhook_docs"
    );
    assert_eq!(resolution.gaps.len(), 1);
    assert_eq!(resolution.next_actions.len(), 2);
}

#[test]
fn adopt_minimal_response_decodes() {
    let body = load("adopt_response_minimal.json");
    let resolution: Resolution =
        serde_json::from_str(&body).expect("minimal Resolution must decode");

    assert_eq!(resolution.prepared_briefing, None);
    assert_eq!(resolution.predicted_response, None);
    assert_eq!(resolution.briefing_token_used, 0);
    assert_eq!(resolution.briefing_token_budget, 1024);
    // Additive WS8 fields default to empty collections when absent.
    assert!(resolution.alternatives_considered.is_empty());
    assert!(resolution.gaps.is_empty());
    assert!(resolution.next_actions.is_empty());
}

#[test]
fn error_fixtures_decode_with_expected_codes() {
    for (file, expected_code) in [
        ("error_codes/adopt_not_found.json", "not_found"),
        (
            "error_codes/adopt_engine_unavailable.json",
            "engine_unavailable",
        ),
        ("error_codes/adopt_invalid_input.json", "invalid_input"),
    ] {
        let body = load(file);
        let err: ErrorBody =
            serde_json::from_str(&body).unwrap_or_else(|e| panic!("{file} decode failed: {e}"));
        assert_eq!(err.code, expected_code, "{file}");
        assert!(!err.message.is_empty(), "{file} has empty message");
    }
}
