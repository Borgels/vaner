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

export type PreparedWorkSourceType = 'work_product' | 'prediction'
export type PreparedWorkKind = 'review' | 'bug' | 'docs' | 'diff' | 'brief' | 'draft' | 'prediction'
export type PreparedWorkActionKind = 'inspect' | 'export' | 'adopt' | 'dismiss' | 'feedback'

export interface PreparedWorkAction {
  kind: PreparedWorkActionKind
  label: string
  tool: string | null
  endpoint: string | null
  arguments: Record<string, unknown>
}

export interface PreparedWorkDiagnosticRef {
  kind: 'file' | 'symbol' | 'doc' | 'record' | 'prediction' | 'work_product' | 'other'
  id: string
  path: string
  reason: string
}

export interface PreparedWorkCard {
  id: string
  source_id: string
  source_type: PreparedWorkSourceType
  kind: PreparedWorkKind
  title: string
  summary: string
  badge: string
  confidence_label: string
  freshness_label: string
  target_label: string
  evidence_count: number
  created_at: number
  updated_at: number
  primary_action: PreparedWorkAction | null
  secondary_actions: PreparedWorkAction[]
  diagnostic_refs: PreparedWorkDiagnosticRef[]
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
  | 'predictions'
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
  daemon_started_at?: number
  workspace_id?: string
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
  focus?: {
    active_workspace_id?: string | null
    status?: string
    explanation?: string
  }
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
  created_at?: number
  last_refreshed_at?: number
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

// ---------------------------------------------------------------------------
// Cockpit refresh: goals, artefacts, predictions-by-state, work-product
// self-eval + lifecycle, learning summary. These mirror the JSON shapes the
// daemon now returns from /goals, /artefacts, /artefacts/{id},
// /predictions/active?include_all=true, /work-products/{id}/inspect, and
// /learning/recent. Field types are intentionally permissive — the engine
// stores some columns as TEXT/JSON-blob and the cockpit only renders the
// subset it understands. See types/setup.ts re: ts-rs migration.
// ---------------------------------------------------------------------------

export type GoalStatus = 'active' | 'paused' | 'abandoned' | 'achieved' | string

export interface Goal {
  id: string
  title: string
  description?: string
  status: GoalStatus
  source?: string
  confidence?: number
  created_at?: number
  updated_at?: number
  related_files?: string[]
  artefact_refs?: string[]
  pc_freshness?: number
  pc_reconciliation_state?: string
  pc_unfinished_item_state?: string
}

export interface Artefact {
  id: string
  title?: string
  status?: string
  connector?: string
  source_uri?: string
  source_tier?: string
  latest_snapshot?: string
  updated_at?: number
  linked_goals?: string[]
  linked_files?: string[]
  kind?: string
}

export interface ArtefactItem {
  id: string
  title?: string
  body?: string
  state?: string
  related_files?: string[]
  related_entities?: string[]
  evidence_refs?: unknown[]
}

export interface ReconciliationOutcome {
  id: string
  artefact_id: string
  pass_at: number
  triggering_signal_id?: string | null
  item_state_deltas_json?: string
  goal_status_deltas_json?: string
  supersedes_json?: string
  evidence_refs_json?: string
}

export interface ArtefactDetail {
  artefact: Artefact
  items: ArtefactItem[]
  snapshot_id: string
  recent_outcomes: ReconciliationOutcome[]
}

export type PredictionReadiness =
  | 'queued'
  | 'grounding'
  | 'evidence_gathering'
  | 'drafting'
  | 'ready'
  | 'stale'
  | string

export interface PredictionSummary {
  id: string
  prediction_id?: string
  title?: string
  prompt?: string
  readiness?: PredictionReadiness
  confidence?: number
  created_at?: number
  updated_at?: number
  invalidation_reason?: string | null
}

export interface PredictionsByState {
  predictions: PredictionSummary[]
  by_state?: Record<PredictionReadiness, PredictionSummary[]>
}

export interface WorkProductSelfEval {
  evidence_coverage: number
  groundedness: number
  contradiction_risk: number
  stale_risk?: number
  reason?: string
}

export interface WorkProductLifecycleEvent {
  event_type: string
  timestamp: number
}

export interface WorkProductInspection {
  id: string
  source_id: string
  source_type: string
  kind: string
  title: string
  summary: string
  body: string
  why_prepared?: string
  confidence_label: string
  freshness_label: string
  freshness_state?: string
  target_label: string
  evidence_count: number
  evidence_refs: Array<{
    kind: string
    path: string
    symbol?: string
    reason?: string
    confidence_label?: string
  }>
  warnings?: string[]
  export_preview?: string
  created_at: number
  updated_at: number
  // Cockpit refresh additions:
  self_eval?: WorkProductSelfEval
  events?: WorkProductLifecycleEvent[]
  status?: string
  adoptability?: string
  feedback_state?: string
}

export interface FeedbackEvent {
  id?: string
  query_id?: string
  timestamp?: number
  cache_tier?: string
  similarity?: number
  quality_lift?: number
  latency_ms?: number
  metadata_json?: string
  feedback_kind?: string
  scenario_id?: string
}

export interface LearningRecent {
  feedback_events: FeedbackEvent[]
  learning_state: Record<string, unknown>
}

export interface LatestInvalidationSignal {
  kind: string
  source: string
  timestamp: number
}

export interface ScenarioInspectorPayload extends ScenarioApiPayload {
  latest_invalidation_signal?: LatestInvalidationSignal | null
}

declare global {
  interface Window {
    __VANER_MODE__?: string
  }
}
