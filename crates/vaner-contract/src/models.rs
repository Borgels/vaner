//! Daemon contract data models.
//!
//! Naming mirrors the Swift `Codable` types in
//! `vaner-desktop/vaner/Services/PredictionModels.swift` 1:1. Field names
//! are snake_case (matching the daemon's Pydantic output) — no
//! camelCase remapping because Rust idiom uses snake_case and the
//! `#[serde(rename_all = "camelCase")]` dance the Swift side does is
//! unnecessary here.
//!
//! Every optional server field uses `Option<T>`; additive fields (lists
//! that may be absent in older daemons) default to empty via
//! `#[serde(default)]` so an older engine response still deserializes.

use serde::{Deserialize, Serialize};

use crate::enums::{HypothesisType, PredictionSource, Readiness, Specificity};

#[cfg(feature = "ts-rs")]
use ts_rs::TS;

// ---------------------------------------------------------------------
// PredictedPrompt tree
// ---------------------------------------------------------------------

/// A single in-flight predicted prompt. The top-level shape returned by
/// `GET /predictions/active` (wrapped in `{"predictions": [...]}`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
pub struct PredictedPrompt {
    pub id: String,
    pub spec: PredictionSpec,
    pub run: PredictionRun,
    pub artifacts: PredictionArtifacts,
}

/// Immutable identity + hypothesis of a predicted prompt.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
pub struct PredictionSpec {
    pub label: String,
    pub description: Option<String>,
    pub source: PredictionSource,
    pub anchor: Option<String>,
    pub confidence: f64,
    pub hypothesis_type: HypothesisType,
    pub specificity: Specificity,
    pub created_at: f64,
}

/// Mutable compute state.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
pub struct PredictionRun {
    pub weight: f64,
    pub token_budget: u64,
    pub tokens_used: u64,
    pub model_calls: u64,
    pub scenarios_spawned: u64,
    pub scenarios_complete: u64,
    pub readiness: Readiness,
    pub updated_at: f64,
}

/// Producer-side artefacts attached during pondering.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
pub struct PredictionArtifacts {
    #[serde(default)]
    pub scenario_ids: Vec<String>,
    #[serde(default)]
    pub evidence_score: f64,
    #[serde(default)]
    pub has_draft: bool,
    #[serde(default)]
    pub has_briefing: bool,
    #[serde(default)]
    pub thinking_trace_count: u64,
}

// ---------------------------------------------------------------------
// Resolution tree (adopt response + future /resolve response)
// ---------------------------------------------------------------------

/// Prepared context package returned by `POST /predictions/{id}/adopt`
/// (and the WS8 `vaner.resolve_query` MCP path).
///
/// The raw server bytes travel alongside this struct, not as a field on
/// it — see [`crate::http::EngineClient::adopt`] which returns
/// `(Resolution, Bytes)`. Keeping raw bytes out of the struct means two
/// `Resolution`s with the same decoded fields compare equal, and the
/// SwiftUI/Svelte diff layer stays honest.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
pub struct Resolution {
    pub intent: String,
    pub confidence: f64,
    pub summary: String,
    #[serde(default)]
    pub evidence: Vec<ResolutionEvidence>,
    pub provenance: Provenance,
    pub resolution_id: String,
    #[serde(default)]
    pub prepared_briefing: Option<String>,
    #[serde(default)]
    pub predicted_response: Option<String>,
    #[serde(default)]
    pub briefing_token_used: u64,
    #[serde(default)]
    pub briefing_token_budget: u64,
    #[serde(default)]
    pub adopted_from_prediction_id: Option<String>,
    /// 0.8.0 WS8: populated by `resolve_query`; empty on the adopt path.
    #[serde(default)]
    pub alternatives_considered: Vec<ResolutionAlternative>,
    #[serde(default)]
    pub gaps: Vec<String>,
    #[serde(default)]
    pub next_actions: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
pub struct ResolutionEvidence {
    #[serde(default)]
    pub id: Option<String>,
    #[serde(default)]
    pub source: Option<String>,
    #[serde(default)]
    pub reason: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
pub struct ResolutionAlternative {
    pub source: String,
    pub reason_rejected: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
pub struct Provenance {
    pub mode: String,
    #[serde(default)]
    pub cache: Option<String>,
    #[serde(default)]
    pub freshness: Option<String>,
}

// ---------------------------------------------------------------------
// /status response
// ---------------------------------------------------------------------

/// Subset of the `/status` response the desktop reducer needs. We
/// intentionally don't model the whole shape — new fields (prediction
/// metrics, calibration) land often and are consumed separately by a
/// diagnostics pane. Serde ignores unknown keys by default.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
pub struct EngineStatus {
    #[serde(default)]
    pub health: Option<String>,
    #[serde(default)]
    pub scenario_counts: Option<ScenarioCounts>,
}

impl EngineStatus {
    /// Heuristic parity with the Swift `reachable` field: true iff the
    /// daemon reports `health == "ok"`.
    #[must_use]
    pub fn reachable(&self) -> bool {
        self.health.as_deref() == Some("ok")
    }

    #[must_use]
    pub fn total_scenarios(&self) -> u64 {
        self.scenario_counts.as_ref().map_or(0, |c| c.total)
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
pub struct ScenarioCounts {
    #[serde(default)]
    pub fresh: u64,
    #[serde(default)]
    pub recent: u64,
    #[serde(default)]
    pub stale: u64,
    #[serde(default)]
    pub total: u64,
}

#[cfg(test)]
mod tests {
    use super::*;

    const PREDICTION_SAMPLE: &str = r#"{
        "id": "p-1",
        "spec": {
            "label": "Write the next test",
            "description": "Predicted follow-up",
            "source": "arc",
            "anchor": "testing",
            "confidence": 0.7,
            "hypothesis_type": "likely_next",
            "specificity": "concrete",
            "created_at": 1762534800.0
        },
        "run": {
            "weight": 0.5,
            "token_budget": 2048,
            "tokens_used": 612,
            "model_calls": 3,
            "scenarios_spawned": 2,
            "scenarios_complete": 1,
            "readiness": "drafting",
            "updated_at": 1762534812.0
        },
        "artifacts": {
            "scenario_ids": ["scen-1"],
            "evidence_score": 0.42,
            "has_draft": true,
            "has_briefing": true,
            "thinking_trace_count": 2
        }
    }"#;

    #[test]
    fn full_prediction_roundtrips() {
        let decoded: PredictedPrompt = serde_json::from_str(PREDICTION_SAMPLE).unwrap();
        assert_eq!(decoded.id, "p-1");
        assert_eq!(decoded.spec.source, PredictionSource::Arc);
        assert_eq!(decoded.run.readiness, Readiness::Drafting);
        assert!(decoded.artifacts.has_draft);

        let reencoded = serde_json::to_string(&decoded).unwrap();
        let again: PredictedPrompt = serde_json::from_str(&reencoded).unwrap();
        assert_eq!(decoded, again);
    }

    #[test]
    fn goal_source_decodes_to_goal_variant() {
        let json = PREDICTION_SAMPLE.replace(r#""source": "arc""#, r#""source": "goal""#);
        let decoded: PredictedPrompt = serde_json::from_str(&json).unwrap();
        assert_eq!(decoded.spec.source, PredictionSource::Goal);
    }

    #[test]
    fn older_engine_response_without_additive_fields_decodes() {
        // Artifacts block dropped entirely — serde's `#[serde(default)]`
        // plus `Option<...>` should still decode cleanly.
        let bare = r#"{
            "id": "p-bare",
            "spec": {
                "label": "x",
                "source": "arc",
                "confidence": 0.1,
                "hypothesis_type": "likely_next",
                "specificity": "concrete",
                "created_at": 0
            },
            "run": {
                "weight": 0, "token_budget": 0, "tokens_used": 0,
                "model_calls": 0, "scenarios_spawned": 0, "scenarios_complete": 0,
                "readiness": "queued", "updated_at": 0
            },
            "artifacts": {}
        }"#;
        let decoded: PredictedPrompt = serde_json::from_str(bare).unwrap();
        assert_eq!(decoded.id, "p-bare");
        assert_eq!(decoded.spec.anchor, None);
        assert_eq!(decoded.spec.description, None);
        assert_eq!(decoded.artifacts.scenario_ids, Vec::<String>::new());
        assert!(!decoded.artifacts.has_draft);
    }

    // Briefing content intentionally avoids `"#` adjacency so the
    // `r#"..."#` delimiter doesn't terminate prematurely. Rust 2024
    // reserves `##` token sequences, so the obvious `r##"..."##`
    // workaround fails to parse.
    const RESOLUTION_SAMPLE: &str = r#"{
        "intent": "Write the next test",
        "confidence": 0.8,
        "summary": "summary",
        "evidence": [],
        "provenance": { "mode": "predictive_hit", "cache": "warm", "freshness": "fresh" },
        "resolution_id": "adopt-p-1",
        "prepared_briefing": "Briefing\nfoo\n",
        "predicted_response": "draft",
        "briefing_token_used": 100,
        "briefing_token_budget": 2048,
        "adopted_from_prediction_id": "p-1"
    }"#;

    #[test]
    fn resolution_decodes_with_optional_fields() {
        let r: Resolution = serde_json::from_str(RESOLUTION_SAMPLE).unwrap();
        assert_eq!(r.resolution_id, "adopt-p-1");
        assert_eq!(r.adopted_from_prediction_id.as_deref(), Some("p-1"));
        assert_eq!(r.provenance.mode, "predictive_hit");
        assert!(r.alternatives_considered.is_empty());
        assert!(r.gaps.is_empty());
    }

    #[test]
    fn resolution_decodes_with_ws8_alternatives() {
        let json = r#"{
            "intent": "x", "confidence": 0.5, "summary": "y",
            "evidence": [],
            "provenance": { "mode": "fresh_resolution" },
            "resolution_id": "r-1",
            "alternatives_considered": [
                { "source": "scn_alt_1", "reason_rejected": "lower score" }
            ],
            "gaps": ["no test coverage"],
            "next_actions": ["add regression test"]
        }"#;
        let r: Resolution = serde_json::from_str(json).unwrap();
        assert_eq!(r.alternatives_considered.len(), 1);
        assert_eq!(r.alternatives_considered[0].source, "scn_alt_1");
        assert_eq!(r.gaps, vec!["no test coverage".to_string()]);
        assert_eq!(r.next_actions, vec!["add regression test".to_string()]);
    }

    #[test]
    fn engine_status_reachable_from_health() {
        let ok: EngineStatus = serde_json::from_str(r#"{"health":"ok"}"#).unwrap();
        assert!(ok.reachable());
        let bad: EngineStatus = serde_json::from_str(r#"{"health":"error"}"#).unwrap();
        assert!(!bad.reachable());
        let absent: EngineStatus = serde_json::from_str(r#"{}"#).unwrap();
        assert!(!absent.reachable());
    }
}
