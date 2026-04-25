// SPDX-License-Identifier: Apache-2.0
// 0.8.6 WS10 — TypeScript mirrors of the WS1/WS2/WS3/WS5 setup primitives.
//
// TODO: replace with ts-rs generated types when vaner-contract ships them.
//
// Hand-written mirrors of:
//   - SetupAnswers          (src/vaner/setup/answers.py)
//   - VanerPolicyBundle     (src/vaner/setup/policy.py)
//   - SelectionResult       (src/vaner/setup/select.py)
//   - AppliedPolicy         (src/vaner/setup/apply.py)
//   - HardwareProfile       (src/vaner/setup/hardware.py)
//
// Field names and Literal vocabularies are byte-aligned with the Python
// dataclasses; downstream WS8 daemon endpoints serialise these in the
// exact shape declared here. WS11 may add ts-rs codegen; until then, treat
// this file as the authoritative cockpit-side schema.

// ---------------------------------------------------------------------------
// Setup answers (WS1)
// ---------------------------------------------------------------------------

export type WorkStyle =
  | 'writing'
  | 'research'
  | 'planning'
  | 'support'
  | 'learning'
  | 'coding'
  | 'general'
  | 'mixed'
  | 'unsure'

export type Priority =
  | 'balanced'
  | 'speed'
  | 'quality'
  | 'privacy'
  | 'cost'
  | 'low_resource'

export type ComputePosture = 'light' | 'balanced' | 'available_power'

export type CloudPosture =
  | 'local_only'
  | 'ask_first'
  | 'hybrid_when_worth_it'
  | 'best_available'

export type BackgroundPosture =
  | 'minimal'
  | 'normal'
  | 'idle_more'
  | 'deep_run_aggressive'

export type HardwareTier = 'light' | 'capable' | 'high_performance' | 'unknown'

export interface SetupAnswers {
  work_styles: WorkStyle[]
  priority: Priority
  compute_posture: ComputePosture
  cloud_posture: CloudPosture
  background_posture: BackgroundPosture
}

// ---------------------------------------------------------------------------
// Policy bundles (WS1)
// ---------------------------------------------------------------------------

export type LocalCloudPosture =
  | 'local_only'
  | 'local_preferred'
  | 'hybrid'
  | 'cloud_preferred'

export type RuntimeProfile = 'small' | 'medium' | 'large' | 'auto'
export type SpendProfile = 'zero' | 'low' | 'medium' | 'high'
export type LatencyProfile = 'snappy' | 'balanced' | 'quality'
export type PrivacyProfile = 'strict' | 'standard' | 'relaxed'

export type PredictionHorizonKey =
  | 'likely_next'
  | 'long_horizon'
  | 'finish_partials'
  | 'balanced'

export type ContextInjectionMode =
  | 'none'
  | 'digest_only'
  | 'adopted_package_only'
  | 'top_match_auto_include'
  | 'policy_hybrid'
  | 'client_controlled'

// Mirrors vaner.intent.deep_run.DeepRunPreset.
export type DeepRunPresetMirror = 'conservative' | 'balanced' | 'aggressive'

export interface VanerPolicyBundle {
  id: string
  label: string
  description: string
  local_cloud_posture: LocalCloudPosture
  runtime_profile: RuntimeProfile
  spend_profile: SpendProfile
  latency_profile: LatencyProfile
  privacy_profile: PrivacyProfile
  prediction_horizon_bias: Record<PredictionHorizonKey, number>
  drafting_aggressiveness: number
  exploration_ratio: number
  persistence_strength: number
  goal_weighting: number
  context_injection_default: ContextInjectionMode
  deep_run_profile: DeepRunPresetMirror
}

// ---------------------------------------------------------------------------
// Selection result (WS3)
// ---------------------------------------------------------------------------

export interface SelectionResult {
  bundle: VanerPolicyBundle
  score: number
  reasons: string[]
  runner_ups: VanerPolicyBundle[]
  forced_fallback: boolean
}

// ---------------------------------------------------------------------------
// Applied policy (WS5)
// ---------------------------------------------------------------------------

// Sentinel prefix used by WS5's apply_policy_bundle when a bundle change
// strictly widens the cloud posture (e.g. local_only -> hybrid). Callers
// detect this via String.prototype.startsWith. Mirrors the Python
// constant in src/vaner/setup/apply.py.
export const WIDENS_CLOUD_POSTURE_SENTINEL = 'WIDENS_CLOUD_POSTURE'

export interface AppliedPolicy {
  // The full VanerConfig is omitted from this mirror — the cockpit only
  // needs the bundle id and the human-readable override audit list. WS8
  // will choose whether to ship the full materialised VanerConfig in the
  // /policy/current response; for now this slim shape covers the
  // BundleSummaryCard's read-only needs.
  bundle_id: string
  overrides_applied: string[]
  // Optional fields populated when the daemon also returns the bundle
  // descriptor + the selection-time runner-ups. These let the cockpit
  // render the "Why this bundle?" disclosure without a second fetch.
  bundle?: VanerPolicyBundle
  selection_reasons?: string[]
  runner_ups?: VanerPolicyBundle[]
}

// ---------------------------------------------------------------------------
// Hardware profile (WS2)
// ---------------------------------------------------------------------------

export type OSKind = 'linux' | 'darwin' | 'windows'
export type CPUClass = 'low' | 'mid' | 'high'
export type GPUKind = 'none' | 'integrated' | 'nvidia' | 'amd' | 'apple_silicon'
export type Runtime = 'ollama' | 'llama.cpp' | 'lmstudio' | 'vllm' | 'mlx'

// Each detected model is serialised as a 3-tuple [runtime, name, size_label].
// Python emits a tuple-of-tuples; FastAPI/MCP serialise tuples as JSON arrays.
export type DetectedModel = [Runtime | string, string, string]

export interface HardwareProfile {
  os: OSKind
  cpu_class: CPUClass
  ram_gb: number
  gpu: GPUKind
  gpu_vram_gb: number | null
  is_battery: boolean
  thermal_constrained: boolean
  detected_runtimes: Runtime[]
  detected_models: DetectedModel[]
  tier: HardwareTier
}

// ---------------------------------------------------------------------------
// Setup status (WS6/WS8 — composite "where is the user in the wizard?")
// ---------------------------------------------------------------------------

export interface SetupStatus {
  // True when the user has completed the wizard at least once and the
  // daemon has a persisted SetupAnswers + selected bundle.
  completed: boolean
  // Optional — present only when `completed` is true.
  answers?: SetupAnswers
  selected_bundle_id?: string
}

export type {}
