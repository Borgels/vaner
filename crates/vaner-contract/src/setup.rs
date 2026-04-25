//! Setup-wizard contract types (0.8.6 follow-up WS12a).
//!
//! Rust mirrors of the 0.8.6 Simple-Mode setup-flow Python types so
//! `vaner-desktop-linux` can consume the generated TypeScript bindings
//! directly via `crates/vaner-contract/bindings/` instead of hand-
//! mirroring the shapes. The Python source-of-truth lives at:
//!
//! - `src/vaner/setup/enums.py` — outcome-level Literal aliases
//! - `src/vaner/setup/answers.py` — `SetupAnswers` dataclass
//! - `src/vaner/setup/policy.py` — `VanerPolicyBundle` dataclass
//! - `src/vaner/setup/hardware.py` — `HardwareProfile` dataclass
//! - `src/vaner/setup/select.py` — `SelectionResult` dataclass
//! - `src/vaner/setup/apply.py` — `AppliedPolicy` dataclass
//! - `src/vaner/models/config.py` — `SetupConfig` / `PolicyConfig`
//! - `src/vaner/intent/deep_run_defaults.py` — `DeepRunDefaults`
//! - `src/vaner/daemon/http.py` — `/setup/questions` payload
//!
//! ## Enum strategy
//!
//! The six *outcome-level* aliases the wizard surfaces (work styles,
//! priorities, postures, hardware tier) are mirrored as Rust enums
//! with `#[serde(rename_all = "snake_case")]` and an `Unknown`
//! catch-all (matching the convention in [`crate::enums`]). Future
//! values land cleanly without breaking deployed clients.
//!
//! The *policy-bundle internal* literal aliases (`local_cloud_posture`,
//! `runtime_profile`, `spend_profile`, `latency_profile`,
//! `privacy_profile`, `context_injection_default`, `deep_run_profile`)
//! are kept as `String` typed fields with a doc comment listing the
//! valid values. Validation lives on the Python side; mirroring those
//! Literal sets here would duplicate state that already lives in
//! `policy.py` and would ratchet up the maintenance cost on every
//! catalogue tweak. Downstream TypeScript consumers can narrow with
//! their own `as const` unions if they need stricter typing.
//!
//! All fields are `#[serde(default)]` where the Python side defaults
//! them, so an older daemon's response keeps decoding when the Rust
//! side adds optional follow-ups.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};

#[cfg(feature = "ts-rs")]
use ts_rs::TS;

// ---------------------------------------------------------------------
// Outcome-level enums (the five wizard questions + hardware tier)
// ---------------------------------------------------------------------

/// What kind of work the user wants Vaner to help with. Multi-select
/// in the wizard; the engine averages priors when more than one is
/// chosen. Mirrors `vaner.setup.enums.WorkStyle`.
#[derive(Debug, Clone, Copy, Default, Eq, PartialEq, Hash, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
#[serde(rename_all = "snake_case")]
pub enum WorkStyle {
    Writing,
    Research,
    Planning,
    Support,
    Learning,
    Coding,
    General,
    #[default]
    Mixed,
    Unsure,
    /// Unknown / future server value.
    #[serde(other)]
    Unknown,
}

/// What the user values most. Single-select. Mirrors
/// `vaner.setup.enums.Priority`.
#[derive(Debug, Clone, Copy, Default, Eq, PartialEq, Hash, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
#[serde(rename_all = "snake_case")]
pub enum Priority {
    #[default]
    Balanced,
    Speed,
    Quality,
    Privacy,
    Cost,
    LowResource,
    /// Unknown / future server value.
    #[serde(other)]
    Unknown,
}

/// How hard the local machine should work. Single-select. Mirrors
/// `vaner.setup.enums.ComputePosture`.
#[derive(Debug, Clone, Copy, Default, Eq, PartialEq, Hash, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
#[serde(rename_all = "snake_case")]
pub enum ComputePosture {
    Light,
    #[default]
    Balanced,
    AvailablePower,
    /// Unknown / future server value.
    #[serde(other)]
    Unknown,
}

/// User's stance on cloud LLM calls. Single-select. Mirrors
/// `vaner.setup.enums.CloudPosture`.
#[derive(Debug, Clone, Copy, Default, Eq, PartialEq, Hash, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
#[serde(rename_all = "snake_case")]
pub enum CloudPosture {
    LocalOnly,
    #[default]
    AskFirst,
    HybridWhenWorthIt,
    BestAvailable,
    /// Unknown / future server value.
    #[serde(other)]
    Unknown,
}

/// Aggressiveness of background pondering. Single-select. Mirrors
/// `vaner.setup.enums.BackgroundPosture`.
#[derive(Debug, Clone, Copy, Default, Eq, PartialEq, Hash, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
#[serde(rename_all = "snake_case")]
pub enum BackgroundPosture {
    Minimal,
    #[default]
    Normal,
    IdleMore,
    DeepRunAggressive,
    /// Unknown / future server value.
    #[serde(other)]
    Unknown,
}

/// Output of WS2's `tier_for()` mapping. Mirrors
/// `vaner.setup.enums.HardwareTier`.
#[derive(Debug, Clone, Copy, Default, Eq, PartialEq, Hash, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
#[serde(rename_all = "snake_case")]
pub enum HardwareTier {
    Light,
    Capable,
    HighPerformance,
    #[default]
    Unknown,
    /// Unknown / future server value (distinct from the documented
    /// `unknown` tier — kept so future server values still decode).
    #[serde(other)]
    Other,
}

// ---------------------------------------------------------------------
// SetupAnswers — wizard output
// ---------------------------------------------------------------------

/// Immutable record of one completed Simple-Mode wizard run. Mirrors
/// `vaner.setup.answers.SetupAnswers` (Python tuples are `Vec` here —
/// the wire shape is a JSON list).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
pub struct SetupAnswers {
    pub work_styles: Vec<WorkStyle>,
    pub priority: Priority,
    pub compute_posture: ComputePosture,
    pub cloud_posture: CloudPosture,
    pub background_posture: BackgroundPosture,
}

// ---------------------------------------------------------------------
// VanerPolicyBundle — outcome-level archetype with engine knobs baked in
// ---------------------------------------------------------------------

/// One outcome-level policy bundle. Mirrors
/// `vaner.setup.policy.VanerPolicyBundle`.
///
/// Internal-literal fields (postures, profiles, deep-run preset) are
/// `String` typed; see the module-level note for the rationale. The
/// expected value sets are documented inline so frontend code can
/// narrow them via `as const` unions if desired.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
pub struct VanerPolicyBundle {
    pub id: String,
    pub label: String,
    pub description: String,

    /// One of: `"local_only"`, `"local_preferred"`, `"hybrid"`,
    /// `"cloud_preferred"`.
    pub local_cloud_posture: String,
    /// One of: `"small"`, `"medium"`, `"large"`, `"auto"`.
    pub runtime_profile: String,
    /// One of: `"zero"`, `"low"`, `"medium"`, `"high"`.
    pub spend_profile: String,
    /// One of: `"snappy"`, `"balanced"`, `"quality"`.
    pub latency_profile: String,
    /// One of: `"strict"`, `"standard"`, `"relaxed"`.
    pub privacy_profile: String,

    /// Frontier-scoring weight distribution over the four horizon
    /// buckets: `"likely_next"`, `"long_horizon"`, `"finish_partials"`,
    /// `"balanced"`. Modeled as a free-form map because the Python
    /// side guards key membership at construction time.
    pub prediction_horizon_bias: HashMap<String, f64>,

    pub drafting_aggressiveness: f64,
    pub exploration_ratio: f64,
    pub persistence_strength: f64,
    pub goal_weighting: f64,

    /// One of: `"none"`, `"digest_only"`, `"adopted_package_only"`,
    /// `"top_match_auto_include"`, `"policy_hybrid"`,
    /// `"client_controlled"`.
    pub context_injection_default: String,
    /// One of the `DeepRunPreset` literals: `"conservative"`,
    /// `"balanced"`, `"aggressive"`.
    pub deep_run_profile: String,
}

// ---------------------------------------------------------------------
// HardwareProfile — detected machine snapshot
// ---------------------------------------------------------------------

/// One detected-runtime row from `HardwareProfile.detected_models`.
/// Python uses a `tuple[str, str, str]` (`(runtime, name, size_label)`);
/// the wire shape that crosses the daemon boundary is a JSON array of
/// three strings, which serde decodes into this struct via tuple-style
/// representation when the array length matches. We expose it as a
/// named struct for ergonomics; frontends can read a tuple if they
/// prefer.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
pub struct DetectedModel {
    pub runtime: String,
    pub name: String,
    pub size_label: String,
}

/// Immutable snapshot of detected hardware capabilities. Mirrors
/// `vaner.setup.hardware.HardwareProfile`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
pub struct HardwareProfile {
    /// One of: `"linux"`, `"darwin"`, `"windows"`.
    pub os: String,
    /// One of: `"low"`, `"mid"`, `"high"`.
    pub cpu_class: String,
    pub ram_gb: u32,
    /// One of: `"none"`, `"integrated"`, `"nvidia"`, `"amd"`,
    /// `"apple_silicon"`.
    pub gpu: String,
    pub gpu_vram_gb: Option<u32>,
    pub is_battery: bool,
    pub thermal_constrained: bool,
    /// Each entry is one of: `"ollama"`, `"llama.cpp"`, `"lmstudio"`,
    /// `"vllm"`, `"mlx"`.
    pub detected_runtimes: Vec<String>,
    /// `[runtime, name, size_label]` triples on the wire.
    pub detected_models: Vec<(String, String, String)>,
    pub tier: HardwareTier,
}

// ---------------------------------------------------------------------
// SelectionResult — output of WS3 select_policy_bundle()
// ---------------------------------------------------------------------

/// Output of `select_policy_bundle`. Mirrors
/// `vaner.setup.select.SelectionResult`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
pub struct SelectionResult {
    pub bundle: VanerPolicyBundle,
    pub score: f64,
    pub reasons: Vec<String>,
    pub runner_ups: Vec<VanerPolicyBundle>,
    pub forced_fallback: bool,
}

// ---------------------------------------------------------------------
// AppliedPolicy — output of WS5 apply_policy_bundle()
// ---------------------------------------------------------------------

/// Result of applying a bundle to a config. Mirrors
/// `vaner.setup.apply.AppliedPolicy`.
///
/// The Python side carries the materialised `VanerConfig` here; that
/// type is too large (and too churn-prone) to mirror in the contract
/// crate. We expose the bundle id and the audit list, which is what
/// the desktop UI's transparency panel actually consumes. The
/// `widens_cloud_posture` boolean is a server-computed convenience
/// derived from the `WIDENS_CLOUD_POSTURE` sentinel string in
/// `overrides_applied`, so the client doesn't have to parse the
/// audit list to detect widening.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
pub struct AppliedPolicy {
    pub bundle_id: String,
    pub overrides_applied: Vec<String>,
    /// `true` iff the new bundle's cloud posture is strictly more
    /// permissive than the previous bundle's. Derived from the
    /// `WIDENS_CLOUD_POSTURE` sentinel in `overrides_applied`.
    #[serde(default)]
    pub widens_cloud_posture: bool,
}

// ---------------------------------------------------------------------
// SetupConfig + PolicyConfig — `[setup]` / `[policy]` sections of
// `.vaner/config.toml`
// ---------------------------------------------------------------------

/// `[setup]` section of the config. Mirrors
/// `vaner.models.config.SetupConfig`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
pub struct SetupConfig {
    /// One of: `"simple"`, `"advanced"`.
    pub mode: String,
    pub work_styles: Vec<WorkStyle>,
    pub priority: Priority,
    pub compute_posture: ComputePosture,
    pub cloud_posture: CloudPosture,
    pub background_posture: BackgroundPosture,
    /// ISO-8601 timestamp of the most recent wizard completion. `None`
    /// means the wizard has never run on this config.
    #[serde(default)]
    pub completed_at: Option<String>,
    pub version: u32,
}

/// `[policy]` section of the config. Mirrors
/// `vaner.models.config.PolicyConfig`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
pub struct PolicyConfig {
    pub selected_bundle_id: String,
    /// Free-form per-knob user overrides on top of the selected
    /// bundle. Keys are bundle field names; values are arbitrary JSON.
    #[serde(default)]
    pub bundle_overrides: HashMap<String, serde_json::Value>,
    pub auto_select: bool,
}

// ---------------------------------------------------------------------
// DeepRunDefaults — pre-fills for the Deep-Run start dialog
// ---------------------------------------------------------------------

/// Seed values for the Deep-Run start dialog. Mirrors
/// `vaner.intent.deep_run_defaults.DeepRunDefaults`.
///
/// Internal-literal fields (`preset`, `horizon_bias`, `locality`,
/// `focus`) are `String` typed because they are owned by the 0.8.3
/// Deep-Run module — the contract crate does not mirror Deep-Run
/// internals.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
pub struct DeepRunDefaults {
    /// One of the `DeepRunPreset` literals.
    pub preset: String,
    /// One of the `DeepRunHorizonBias` literals.
    pub horizon_bias: String,
    /// One of the `DeepRunLocality` literals.
    pub locality: String,
    pub cost_cap_usd: f64,
    /// One of the `DeepRunFocus` literals.
    pub focus: String,
    pub source_bundle_id: String,
    pub reasons: Vec<String>,
}

// ---------------------------------------------------------------------
// SetupQuestion — payload of `GET /setup/questions` /
// `vaner.setup.questions` MCP tool
// ---------------------------------------------------------------------

/// One choice for a `SetupQuestion`. Mirrors the dict shape under
/// `_SETUP_QUESTIONS_PAYLOAD["questions"][N]["choices"][M]` in
/// `vaner.daemon.http`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
pub struct SetupQuestionOption {
    pub value: String,
    pub label: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
}

/// One Simple-Mode question. Mirrors the dict shape in
/// `_SETUP_QUESTIONS_PAYLOAD["questions"][N]`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS), ts(export))]
pub struct SetupQuestion {
    pub id: String,
    pub prompt: String,
    /// One of: `"single"`, `"multi"`.
    pub kind: String,
    pub options: Vec<SetupQuestionOption>,
    /// String for `"single"` questions, list of strings for `"multi"`.
    /// Modeled as free-form JSON because the wire shape varies.
    #[serde(default)]
    pub default: Option<serde_json::Value>,
}

// ---------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn setup_answers_roundtrips() {
        let raw = r#"{
            "work_styles": ["coding", "research"],
            "priority": "quality",
            "compute_posture": "available_power",
            "cloud_posture": "hybrid_when_worth_it",
            "background_posture": "deep_run_aggressive"
        }"#;
        let decoded: SetupAnswers = serde_json::from_str(raw).unwrap();
        assert_eq!(decoded.work_styles, vec![WorkStyle::Coding, WorkStyle::Research]);
        assert_eq!(decoded.priority, Priority::Quality);
        assert_eq!(decoded.compute_posture, ComputePosture::AvailablePower);
        assert_eq!(decoded.cloud_posture, CloudPosture::HybridWhenWorthIt);
        assert_eq!(decoded.background_posture, BackgroundPosture::DeepRunAggressive);

        let reencoded = serde_json::to_string(&decoded).unwrap();
        let again: SetupAnswers = serde_json::from_str(&reencoded).unwrap();
        assert_eq!(decoded, again);
    }

    #[test]
    fn unknown_enum_values_decode_to_unknown() {
        let raw = r#"{
            "work_styles": ["from_the_future"],
            "priority": "from_the_future",
            "compute_posture": "from_the_future",
            "cloud_posture": "from_the_future",
            "background_posture": "from_the_future"
        }"#;
        let decoded: SetupAnswers = serde_json::from_str(raw).unwrap();
        assert_eq!(decoded.work_styles, vec![WorkStyle::Unknown]);
        assert_eq!(decoded.priority, Priority::Unknown);
        assert_eq!(decoded.compute_posture, ComputePosture::Unknown);
        assert_eq!(decoded.cloud_posture, CloudPosture::Unknown);
        assert_eq!(decoded.background_posture, BackgroundPosture::Unknown);
    }

    #[test]
    fn hardware_tier_decodes_known_and_unknown() {
        for (raw, expected) in [
            (r#""light""#, HardwareTier::Light),
            (r#""capable""#, HardwareTier::Capable),
            (r#""high_performance""#, HardwareTier::HighPerformance),
            (r#""unknown""#, HardwareTier::Unknown),
        ] {
            let decoded: HardwareTier = serde_json::from_str(raw).unwrap();
            assert_eq!(decoded, expected, "decoding {raw}");
        }
        let other: HardwareTier = serde_json::from_str(r#""brand_new_tier""#).unwrap();
        assert_eq!(other, HardwareTier::Other);
    }

    #[test]
    fn hardware_profile_roundtrips() {
        let raw = r#"{
            "os": "linux",
            "cpu_class": "high",
            "ram_gb": 64,
            "gpu": "nvidia",
            "gpu_vram_gb": 24,
            "is_battery": false,
            "thermal_constrained": false,
            "detected_runtimes": ["ollama", "llama.cpp"],
            "detected_models": [["ollama", "llama3.1:8b", "4.7GB"]],
            "tier": "high_performance"
        }"#;
        let decoded: HardwareProfile = serde_json::from_str(raw).unwrap();
        assert_eq!(decoded.os, "linux");
        assert_eq!(decoded.ram_gb, 64);
        assert_eq!(decoded.gpu_vram_gb, Some(24));
        assert_eq!(decoded.detected_models.len(), 1);
        assert_eq!(decoded.detected_models[0].1, "llama3.1:8b");
        assert_eq!(decoded.tier, HardwareTier::HighPerformance);
    }

    #[test]
    fn policy_bundle_decodes_with_horizon_map() {
        let raw = r#"{
            "id": "hybrid_balanced",
            "label": "Hybrid Balanced",
            "description": "Sensible default",
            "local_cloud_posture": "hybrid",
            "runtime_profile": "medium",
            "spend_profile": "low",
            "latency_profile": "balanced",
            "privacy_profile": "standard",
            "prediction_horizon_bias": {
                "likely_next": 1.0,
                "long_horizon": 0.5,
                "finish_partials": 0.5,
                "balanced": 1.0
            },
            "drafting_aggressiveness": 1.0,
            "exploration_ratio": 0.5,
            "persistence_strength": 1.0,
            "goal_weighting": 1.0,
            "context_injection_default": "policy_hybrid",
            "deep_run_profile": "balanced"
        }"#;
        let decoded: VanerPolicyBundle = serde_json::from_str(raw).unwrap();
        assert_eq!(decoded.id, "hybrid_balanced");
        assert_eq!(decoded.prediction_horizon_bias.len(), 4);
        assert_eq!(
            decoded.prediction_horizon_bias.get("likely_next"),
            Some(&1.0)
        );
    }

    #[test]
    fn setup_config_decodes_with_optional_completed_at() {
        let raw = r#"{
            "mode": "simple",
            "work_styles": ["mixed"],
            "priority": "balanced",
            "compute_posture": "balanced",
            "cloud_posture": "ask_first",
            "background_posture": "normal",
            "version": 1
        }"#;
        let decoded: SetupConfig = serde_json::from_str(raw).unwrap();
        assert_eq!(decoded.mode, "simple");
        assert_eq!(decoded.completed_at, None);
        assert_eq!(decoded.version, 1);
    }

    #[test]
    fn policy_config_decodes_with_overrides() {
        let raw = r#"{
            "selected_bundle_id": "hybrid_balanced",
            "bundle_overrides": {"context_injection_mode": "digest_only"},
            "auto_select": true
        }"#;
        let decoded: PolicyConfig = serde_json::from_str(raw).unwrap();
        assert_eq!(decoded.selected_bundle_id, "hybrid_balanced");
        assert!(decoded.auto_select);
        assert_eq!(decoded.bundle_overrides.len(), 1);
    }

    #[test]
    fn deep_run_defaults_roundtrips() {
        let raw = r#"{
            "preset": "balanced",
            "horizon_bias": "balanced",
            "locality": "local_preferred",
            "cost_cap_usd": 1.0,
            "focus": "active_goals",
            "source_bundle_id": "hybrid_balanced",
            "reasons": ["Bundle hybrid_balanced -> preset balanced"]
        }"#;
        let decoded: DeepRunDefaults = serde_json::from_str(raw).unwrap();
        assert_eq!(decoded.preset, "balanced");
        assert_eq!(decoded.cost_cap_usd, 1.0);
        assert_eq!(decoded.reasons.len(), 1);
    }

    #[test]
    fn setup_question_decodes() {
        let raw = r#"{
            "id": "priority",
            "prompt": "What matters most?",
            "kind": "single",
            "options": [
                {"value": "balanced", "label": "Balanced"},
                {"value": "speed", "label": "Speed", "description": "Snappy responses"}
            ],
            "default": "balanced"
        }"#;
        let decoded: SetupQuestion = serde_json::from_str(raw).unwrap();
        assert_eq!(decoded.id, "priority");
        assert_eq!(decoded.options.len(), 2);
        assert_eq!(decoded.options[1].description.as_deref(), Some("Snappy responses"));
        assert_eq!(decoded.default.as_ref().and_then(|v| v.as_str()), Some("balanced"));
    }

    #[test]
    fn applied_policy_widens_flag_defaults_false() {
        let raw = r#"{
            "bundle_id": "local_balanced",
            "overrides_applied": ["BackendConfig.prefer_local: true"]
        }"#;
        let decoded: AppliedPolicy = serde_json::from_str(raw).unwrap();
        assert_eq!(decoded.bundle_id, "local_balanced");
        assert!(!decoded.widens_cloud_posture);
    }
}
