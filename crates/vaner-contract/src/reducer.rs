//! Pure state reducer mirroring `StateReducer.swift`.
//!
//! The precedence chain is the single source of truth for "what should
//! the desktop popover show right now?". Any UI implementation feeds
//! the reducer the inputs it has; the reducer returns one `VanerState`
//! variant describing the screen to render. Identical logic on every
//! platform means the same inputs always produce the same UX.
//!
//! Precedence (highest wins):
//!
//! 1. `NoAccess` — engine unreachable.
//! 2. `PermissionNeeded` — one or more sources are blocked.
//! 3. `InstalledNotConnected` — no sources at all.
//! 4. `Learning` — engine is still indexing.
//! 5. `ActivePredictions` — at least one prediction is drafting/ready
//!    AND an agent is running.
//! 6. `NoActiveAgent` — predictions exist (or prepared moments exist)
//!    but no agent is running — adopting would land in a blank void.
//! 7. `Prepared` — a reactive prepared moment with agent running.
//! 8. `Watching` — idle state; daemon alive, no surfaced work.

use serde::{Deserialize, Serialize};

#[cfg(feature = "ts-rs")]
use ts_rs::TS;

use crate::models::PredictedPrompt;

/// Simplified flag bundle the reducer consumes. Callers compose these
/// from their platform-specific observations (agent detection, source
/// status, engine health).
#[derive(Debug, Clone)]
pub struct ReducerInputs {
    pub engine_reachable: bool,
    pub has_blocked_sources: bool,
    pub has_any_source: bool,
    pub is_indexing: bool,
    pub has_prepared_lead: bool,
    pub any_agent_running: bool,
    pub active_predictions: Vec<PredictedPrompt>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum VanerState {
    Error,
    PermissionNeeded,
    InstalledNotConnected,
    Learning,
    ActivePredictions { predictions: Vec<PredictedPrompt> },
    NoActiveAgent { pending_count: u64 },
    Prepared,
    Watching,
}

/// Run the reducer. Pure function — no I/O, no side effects.
#[must_use]
pub fn reduce(inputs: &ReducerInputs) -> VanerState {
    if !inputs.engine_reachable {
        return VanerState::Error;
    }
    if inputs.has_blocked_sources {
        return VanerState::PermissionNeeded;
    }
    if !inputs.has_any_source {
        return VanerState::InstalledNotConnected;
    }
    if inputs.is_indexing {
        return VanerState::Learning;
    }

    // 0.8.0: predictions in drafting/ready outrank a reactive prepared
    // moment. Only the adoptable subset surfaces — queued/grounding
    // stay hidden behind the existing Prepared/Watching path.
    let mut adoptable: Vec<PredictedPrompt> = inputs
        .active_predictions
        .iter()
        .filter(|p| p.run.readiness.is_adoptable())
        .cloned()
        .collect();

    if !adoptable.is_empty() {
        if !inputs.any_agent_running {
            // Clicking Adopt would stash to a path nothing reads. Tell
            // the user to launch an agent before engaging.
            return VanerState::NoActiveAgent {
                pending_count: adoptable.len() as u64,
            };
        }
        // Sort: Ready before Drafting, then confidence desc within each
        // group. Same ordering as Swift reducer.
        adoptable.sort_by(|a, b| {
            use crate::enums::Readiness::{Drafting, Ready};
            let rank = |p: &PredictedPrompt| match p.run.readiness {
                Ready => 0,
                Drafting => 1,
                _ => 2,
            };
            rank(a)
                .cmp(&rank(b))
                .then_with(|| b.spec.confidence.total_cmp(&a.spec.confidence))
        });
        return VanerState::ActivePredictions {
            predictions: adoptable,
        };
    }

    if inputs.has_prepared_lead {
        if !inputs.any_agent_running {
            return VanerState::NoActiveAgent { pending_count: 1 };
        }
        return VanerState::Prepared;
    }

    VanerState::Watching
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::enums::{HypothesisType, PredictionSource, Readiness, Specificity};
    use crate::models::{PredictionArtifacts, PredictionRun, PredictionSpec};

    fn pred(id: &str, readiness: Readiness, confidence: f64) -> PredictedPrompt {
        PredictedPrompt {
            id: id.to_string(),
            spec: PredictionSpec {
                label: format!("label-{id}"),
                description: None,
                source: PredictionSource::Arc,
                anchor: None,
                confidence,
                hypothesis_type: HypothesisType::LikelyNext,
                specificity: Specificity::Concrete,
                created_at: 0.0,
            },
            run: PredictionRun {
                weight: 0.5,
                token_budget: 1024,
                tokens_used: 100,
                model_calls: 0,
                scenarios_spawned: 0,
                scenarios_complete: 0,
                readiness,
                updated_at: 0.0,
            },
            artifacts: PredictionArtifacts::default(),
            readiness_label: None,
            eta_bucket: None,
            eta_bucket_label: None,
            adoptable: None,
            rank: None,
            ui_summary: None,
            suppression_reason: None,
            source_label: None,
        }
    }

    fn base_inputs() -> ReducerInputs {
        ReducerInputs {
            engine_reachable: true,
            has_blocked_sources: false,
            has_any_source: true,
            is_indexing: false,
            has_prepared_lead: false,
            any_agent_running: true,
            active_predictions: vec![],
        }
    }

    #[test]
    fn engine_unreachable_wins_over_everything() {
        let state = reduce(&ReducerInputs {
            engine_reachable: false,
            active_predictions: vec![pred("p", Readiness::Ready, 0.9)],
            has_prepared_lead: true,
            ..base_inputs()
        });
        assert!(matches!(state, VanerState::Error));
    }

    #[test]
    fn blocked_sources_win_over_predictions() {
        let state = reduce(&ReducerInputs {
            has_blocked_sources: true,
            active_predictions: vec![pred("p", Readiness::Ready, 0.9)],
            ..base_inputs()
        });
        assert!(matches!(state, VanerState::PermissionNeeded));
    }

    #[test]
    fn learning_wins_over_predictions() {
        let state = reduce(&ReducerInputs {
            is_indexing: true,
            active_predictions: vec![pred("p", Readiness::Ready, 0.9)],
            ..base_inputs()
        });
        assert!(matches!(state, VanerState::Learning));
    }

    #[test]
    fn ready_prediction_outranks_prepared_lead() {
        let state = reduce(&ReducerInputs {
            has_prepared_lead: true,
            active_predictions: vec![pred("p", Readiness::Ready, 0.9)],
            ..base_inputs()
        });
        assert!(matches!(
            state,
            VanerState::ActivePredictions { predictions } if predictions.len() == 1
        ));
    }

    #[test]
    fn predictions_sort_ready_before_drafting_then_confidence() {
        let state = reduce(&ReducerInputs {
            active_predictions: vec![
                pred("d-low", Readiness::Drafting, 0.2),
                pred("r-low", Readiness::Ready, 0.5),
                pred("r-high", Readiness::Ready, 0.9),
                pred("d-high", Readiness::Drafting, 0.8),
            ],
            ..base_inputs()
        });
        let VanerState::ActivePredictions { predictions } = state else {
            panic!("expected ActivePredictions");
        };
        let ids: Vec<&str> = predictions.iter().map(|p| p.id.as_str()).collect();
        assert_eq!(ids, vec!["r-high", "r-low", "d-high", "d-low"]);
    }

    #[test]
    fn ready_prediction_without_agent_routes_to_no_active_agent() {
        let state = reduce(&ReducerInputs {
            any_agent_running: false,
            active_predictions: vec![pred("p", Readiness::Ready, 0.9)],
            ..base_inputs()
        });
        assert!(matches!(
            state,
            VanerState::NoActiveAgent { pending_count: 1 }
        ));
    }

    #[test]
    fn non_adoptable_predictions_fall_through_to_prepared() {
        let state = reduce(&ReducerInputs {
            has_prepared_lead: true,
            active_predictions: vec![
                pred("q", Readiness::Queued, 0.9),
                pred("g", Readiness::Grounding, 0.9),
                pred("e", Readiness::EvidenceGathering, 0.9),
                pred("s", Readiness::Stale, 0.9),
            ],
            ..base_inputs()
        });
        assert!(matches!(state, VanerState::Prepared));
    }

    #[test]
    fn empty_everything_watching() {
        let state = reduce(&base_inputs());
        assert!(matches!(state, VanerState::Watching));
    }
}
