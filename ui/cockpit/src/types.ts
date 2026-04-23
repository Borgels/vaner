export type UIScenarioKind = 'research' | 'explain' | 'change' | 'debug' | 'refactor'
export type UIFreshness = 'fresh' | 'recent' | 'stale'
export type UIDecisionState = 'active' | 'chosen' | 'partial' | 'rejected' | 'pending' | 'idle'
export type UIAccent = 'violet' | 'amber' | 'teal'
export type UIMode = 'daemon' | 'proxy' | 'mcp'

export interface UIScenario {
  id: string
  kind: UIScenarioKind
  title: string
  score: number
  freshness: UIFreshness
  depth: number
  parent: string | null
  path: string
  skill: string | null
  decisionState: UIDecisionState
  reason: string
  entities: string[]
  pinned: boolean
}

export interface UIEvidence {
  file: string
  lines: string | null
  note: string
  startLine: number | null
  endLine: number | null
}

export interface UISkill {
  name: string
  desc: string
  weight: number
}

export interface UIPinnedFact {
  id: string
  text: string
}

export type PipelineStage =
  | 'signals'
  | 'targets'
  | 'model'
  | 'artefacts'
  | 'scenarios'
  | 'decisions'
  | 'prediction'
  | 'calibration'
  | 'draft'
  | 'budget'
  | 'system'

export interface UIEvent {
  id: string
  t: string
  tag: string
  color: string
  msg: string
  scn: string | null
  stage?: PipelineStage
  kind?: string
  ts?: number
  path?: string | null
  cycleId?: string | null
  payload?: Record<string, unknown>
}

export interface UIPackageState {
  id: string
  tokens: number
  budget: number
  chosen: number
  partial: number
  rejected: number
  compression: number
}

export interface BackendSettings {
  name: string
  base_url: string
  model: string
  api_key_env: string
  prefer_local: boolean
  fallback_enabled: boolean
  fallback_base_url: string
  fallback_model: string
  fallback_api_key_env: string
  remote_budget_per_hour: number
}

export interface ComputeSettings {
  device: string
  cpu_fraction: number
  gpu_memory_fraction: number
  idle_only: boolean
  idle_cpu_threshold: number
  idle_gpu_threshold: number
  embedding_device: string | null
  exploration_concurrency: number
  max_parallel_precompute: number
  max_cycle_seconds: number
  max_session_minutes: number | null
}

export interface MCPSettings {
  transport: 'stdio' | 'sse'
  http_host: string
  http_port: number
}

export interface LimitSettings {
  max_age_seconds: number
  max_context_tokens: number
}

export interface CockpitSettings {
  density: 'relaxed' | 'dense'
  accent: UIAccent
  reduceMotion: boolean
  topK: number
  gatewayEnabled: boolean
}

export interface BackendPreset {
  name: string
  base_url: string
  default_model: string
  api_key_env: string
}

export interface ComputeDevice {
  id: string
  label: string
  kind: string
  total_memory_bytes?: number
}

export interface BootstrapPayload {
  mode: UIMode
  version?: string
  cockpit_sha?: string
}

export interface StatusPayload {
  health: string
  mode?: UIMode
  gateway_enabled?: boolean
  compute: Partial<ComputeSettings> & { device?: string }
  backend?: Partial<BackendSettings>
  mcp?: Partial<MCPSettings>
  limits?: Partial<LimitSettings>
  scenario_counts?: {
    fresh: number
    recent: number
    stale: number
    total: number
  }
  top_scenario?: string | null
  prediction_metrics?: {
    next_prompt_top1_rate?: number
    next_prompt_top3_rate?: number
    next_prompt_logloss?: number
    next_prompt_brier?: number
    draft_usefulness_rate?: number
    budget_utilization?: number
    predictive_lead_seconds_avg?: number
    confidence_conditioned_utility?: number
    bucket_budgets?: BucketBudgets
  }
  prediction_calibration?: Array<{
    bucket: number
    confidence_mid: number
    count: number
    accuracy: number
  }>
}

export interface BucketBudgets {
  exploit: BudgetBreakdown
  hedge: BudgetBreakdown
  invest: BudgetBreakdown
  no_regret: BudgetBreakdown
}

export interface BudgetBreakdown {
  allocated_ms: number
  used_ms: number
}

export interface ImpactSummary {
  count: number
  mean_latency_gain_ms?: number
  mean_char_delta?: number
  idle_seconds_used?: number
}

export interface DecisionRecordPayload {
  id: string
  prompt: string
  prompt_hash: string
  assembled_at: number
  cache_tier: string
  partial_similarity: number
  token_budget: number
  token_used: number
  notes: string[]
  selection_count?: number
  selections: Array<{
    artefact_key: string
    source_path: string
    final_score: number
    token_count: number
    stale: boolean
    kept: boolean
    drop_reason?: string | null
    rationale: string
  }>
}

export interface ScenarioApiPayload {
  id: string
  kind: UIScenarioKind
  score: number
  freshness: UIFreshness
  entities: string[]
  evidence: Array<{
    key?: string
    source_path?: string
    excerpt?: string
    weight?: number
    start_line?: number | null
    end_line?: number | null
  }>
  prepared_context?: string
  coverage_gaps?: string[]
  last_outcome?: string | null
  memory_state?: string
  pinned?: number | boolean
  title?: string
  path?: string
  depth?: number
  parent?: string | null
  skill?: string | null
  decision_state?: UIDecisionState
  reason?: string
  score_components?: Array<{ label: string; value: number; description?: string }>
}

declare global {
  interface Window {
    __VANER_MODE__?: string
  }
}
