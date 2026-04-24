//! String enums from the daemon contract. All are **unknown-tolerant**:
//! unknown values deserialize to a sentinel variant rather than erroring,
//! so new server-side values never crash a deployed desktop client. The
//! Swift side does the same thing via a custom `init(from:)` that falls
//! back on unknown `rawValue`s; see
//! `vaner-desktop/vaner/Services/PredictionModels.swift`.
//!
//! Serialization uses the literal snake_case strings the daemon emits.

use serde::{Deserialize, Serialize};

#[cfg(feature = "ts-rs")]
use ts_rs::TS;

/// Origin of a predicted prompt. New values land here over time; the
/// `Unknown` catch-all keeps decoding from failing. The Swift mirror
/// collapses unknown values to `.history`; the Rust side exposes a
/// dedicated variant so callers can tell them apart if they want.
#[derive(Debug, Clone, Copy, Eq, PartialEq, Hash, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
#[serde(rename_all = "snake_case")]
pub enum PredictionSource {
    Arc,
    Pattern,
    LlmBranch,
    Macro,
    History,
    /// 0.8.0 WS7: prediction anchored to a `WorkspaceGoal`.
    Goal,
    /// Unknown / future server value. `PredictionSpec.anchor` may still
    /// carry useful info even when the source is opaque.
    #[serde(other)]
    Unknown,
}

impl Default for PredictionSource {
    fn default() -> Self {
        Self::History
    }
}

/// Hypothesis shape. Drives the prefix rendered in the UI row
/// (`Next ›`, `Maybe ›`, `If… ›`).
#[derive(Debug, Clone, Copy, Eq, PartialEq, Hash, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
#[serde(rename_all = "snake_case")]
pub enum HypothesisType {
    LikelyNext,
    PossibleBranch,
    LongTail,
    #[serde(other)]
    Unknown,
}

impl Default for HypothesisType {
    fn default() -> Self {
        Self::LongTail
    }
}

/// How precise the prediction is. Interacts with `HypothesisType` in the
/// row prefix matrix.
#[derive(Debug, Clone, Copy, Eq, PartialEq, Hash, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
#[serde(rename_all = "snake_case")]
pub enum Specificity {
    Concrete,
    Category,
    Anchor,
    #[serde(other)]
    Unknown,
}

impl Default for Specificity {
    fn default() -> Self {
        Self::Anchor
    }
}

/// Readiness lifecycle for a prediction. Only `Drafting` and `Ready` are
/// adoptable — anything else renders in the row but shows a disabled
/// Adopt button. Matches the Swift `Readiness` enum's `isAdoptable`
/// computed property.
#[derive(Debug, Clone, Copy, Eq, PartialEq, Hash, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
#[serde(rename_all = "snake_case")]
pub enum Readiness {
    Queued,
    Grounding,
    EvidenceGathering,
    Drafting,
    Ready,
    Stale,
    #[serde(other)]
    Unknown,
}

impl Default for Readiness {
    fn default() -> Self {
        Self::Queued
    }
}

impl Readiness {
    /// Whether the user can click Adopt on a row with this readiness.
    /// Mirrors the Swift `Readiness.isAdoptable` convenience.
    #[must_use]
    pub fn is_adoptable(self) -> bool {
        matches!(self, Self::Drafting | Self::Ready)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decodes_known_prediction_sources() {
        let cases = [
            (r#""arc""#, PredictionSource::Arc),
            (r#""pattern""#, PredictionSource::Pattern),
            (r#""llm_branch""#, PredictionSource::LlmBranch),
            (r#""macro""#, PredictionSource::Macro),
            (r#""history""#, PredictionSource::History),
            (r#""goal""#, PredictionSource::Goal),
        ];
        for (raw, expected) in cases {
            let decoded: PredictionSource = serde_json::from_str(raw).unwrap();
            assert_eq!(decoded, expected, "decoding {raw}");
        }
    }

    #[test]
    fn unknown_prediction_source_becomes_unknown_variant() {
        let decoded: PredictionSource = serde_json::from_str(r#""brand_new""#).unwrap();
        assert_eq!(decoded, PredictionSource::Unknown);
    }

    #[test]
    fn unknown_hypothesis_type_does_not_error() {
        let decoded: HypothesisType = serde_json::from_str(r#""moonshot""#).unwrap();
        assert_eq!(decoded, HypothesisType::Unknown);
    }

    #[test]
    fn unknown_readiness_does_not_error() {
        let decoded: Readiness = serde_json::from_str(r#""exfiltrating""#).unwrap();
        assert_eq!(decoded, Readiness::Unknown);
    }

    #[test]
    fn readiness_adoptable_gate() {
        assert!(Readiness::Drafting.is_adoptable());
        assert!(Readiness::Ready.is_adoptable());
        assert!(!Readiness::Queued.is_adoptable());
        assert!(!Readiness::Grounding.is_adoptable());
        assert!(!Readiness::EvidenceGathering.is_adoptable());
        assert!(!Readiness::Stale.is_adoptable());
        assert!(!Readiness::Unknown.is_adoptable());
    }
}
