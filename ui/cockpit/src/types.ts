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
  | 'work'
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
  focus?: FocusStatePayload
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

export interface FocusDetectedClient {
  id: string
  kind?: string
  display_name?: string
  source?: string
  running?: boolean
  foreground?: boolean
  integration_state?: string
  workspace_hints?: string[]
  observed_at?: number
  focus_confidence?: number
  reason_code?: string
  explanation?: string
  selected?: boolean
  preferred?: boolean
  eligible?: boolean
}

export interface FocusWorkspace {
  id: string
  display_name?: string
  canonical_path?: string
  trust_state?: string
  integration_state?: string
  tier?: string
  pinned?: boolean
  paused?: boolean
  selected?: boolean
  eligible?: boolean
  last_activity_at?: number
  focus_confidence?: number
  work_value_confidence?: number
  eligibility_reasons?: string[]
  blocked_reasons?: string[]
}

export interface FocusWhyNot {
  reason_code?: string
  message?: string
  action?: string
}

export interface FocusStatePayload {
  mode?: string
  status?: string
  resource_mode?: string
  focus_epoch?: number
  active_workspace_id?: string | null
  active_client_id?: string | null
  selected_by?: string
  detected_clients?: FocusDetectedClient[]
  workspaces?: FocusWorkspace[]
  explanation?: string
  why_not?: FocusWhyNot[]
  proactive_allowed?: boolean
}

export interface FocusRoutePayload {
  effective_route?: {
    workspace?: FocusWorkspace
    client?: FocusDetectedClient
    workspace_policy?: string
    automatic?: boolean
    resource_mode?: string
    device?: string
    backend?: Partial<BackendSettings>
    selected_by?: string
    expires_at?: number | null
  }
  workspace_options?: FocusWorkspace[]
  client_options?: FocusDetectedClient[]
  hardware_options?: {
    resource_modes?: Array<{ id: string; label?: string; explanation?: string }>
    devices?: Array<{
      id: string
      kind?: string
      name?: string
      memory_display_gb?: number
      allowed_for_background?: boolean
      explanation?: string
    }>
    runtimes?: Array<{
      id: string
      kind?: string
      endpoint?: string | null
      healthy?: boolean
      selected?: boolean
      explanation?: string
    }>
    models?: Array<{
      id: string
      runtime_id?: string
      name?: string
      size_label?: string
      local?: boolean
    }>
    current?: {
      resource_mode?: string
      device?: string
      backend?: Partial<BackendSettings>
    }
  }
  diagnostics?: {
    focus?: FocusStatePayload
    why_not?: FocusWhyNot[]
    warnings?: string[]
  }
  explanation?: string
}

export interface FocusJob {
  id: string
  job_class: string
  workspace_id?: string
  focus_epoch?: number
  priority?: string
  cancellable?: boolean
  status: string
  defer_reason?: string
  explanation?: string
}

export interface JobsStatusPayload {
  jobs: FocusJob[]
  focus_epoch?: number
  worker?: {
    state?: string
    phase?: string
    pid?: number
    last_heartbeat_at?: number
    cycle_id?: string | null
    defer_reason?: string | null
    explanation?: string | null
  }
  queue?: {
    size?: number
    max_size?: number
    dropped?: number
    coalesced?: number
  }
  profile?: Record<string, number>
}

export interface SourcesPermissionsPayload {
  workspace: string
  privacy_boundary: Record<string, string>
  sources: Record<string, {
    id: string
    enabled: boolean
    default?: boolean
    privacy_zone?: string
    selected_clients?: string[]
    accepted_count?: number
    skipped_count?: number
    ingested_count?: number
    accepted_examples?: Array<{ display_path?: string; path: string; client: string; status: string; reason: string; matched_by?: string | null }>
    skipped_examples?: Array<{ display_path?: string; path: string; client: string; status: string; reason: string; matched_by?: string | null }>
  }>
  updated_at?: number
}

export interface SignalCapabilitiesPayload {
  composer_adapters: Array<{
    host_app: string
    level: string
    emits: string[]
    official_pre_enter_available: boolean
    reason: string
  }>
  privacy_boundary: Record<string, string>
}

export interface RecentActivityItem {
  id: string
  session_id: string
  timestamp: number
  query_text: string
  selected_paths?: string[]
  hit_precomputed?: boolean
  token_used?: number
  corpus_id?: string
  source?: string | null
  host_app?: string | null
  source_event_id?: string | null
  prompt_hash?: string | null
  turn_id?: string | null
  capture_policy?: string | null
  prediction_id?: string | null
}

export interface RecentActivityPayload {
  items: RecentActivityItem[]
  count: number
}

export interface RecentEventPayload {
  id?: string
  ts?: number
  stage?: PipelineStage
  kind?: string
  payload?: Record<string, unknown> | unknown[]
  scn?: string | null
  path?: string | null
  cycle_id?: string | null
  t?: string
  tag?: string
  color?: string
  msg?: string
}

export interface RecentEventsPayload {
  events: RecentEventPayload[]
  count: number
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
  label?: string
  display_label?: string
  title?: string
  prompt?: string
  readiness?: PredictionReadiness
  confidence?: number
  created_at?: number
  updated_at?: number
  invalidation_reason?: string | null
  source_label?: string
  ui_summary?: string
  readiness_label?: string
  evidence_targets?: string[]
  watched_sources?: string[]
  adoptable?: boolean
  match_state?: 'strong_match' | 'weak_match' | 'unrelated' | 'stale' | string
  match_reason?: string
  recommended_action?: 'adopt' | 'inspect' | 'ignore' | string
  snapshot_freshness?: 'ready' | 'warming' | 'stale' | 'cold' | string
  freshness?: string
  trust_status?: string
  spec?: {
    label?: string
    description?: string
    confidence?: number
    source?: string
    structured?: {
      evidence_targets?: string[]
      [key: string]: unknown
    }
  }
  run?: {
    readiness?: PredictionReadiness
    updated_at?: number
  }
}

export interface PredictionsByState {
  predictions: PredictionSummary[]
  by_state?: Record<PredictionReadiness, PredictionSummary[]>
}

export interface PlanDraftSummary {
  id: string
  title: string
  summary: string
  tasks: string[]
  source_client: string
  session_id: string
  turn_id: string
  workspace_id: string
  source_event_id: string
  status: string
  accepted: boolean
  created_at: number
  updated_at: number
  prep_dir: string
}

export interface ActiveWorkPayload {
  summary: string
  loop: 'ponder' | 'answer' | 'idle' | string
  phase: string
  worker?: Record<string, unknown>
  queue?: Record<string, unknown>
  jobs?: FocusJob[]
  resources?: Record<string, unknown>
  utilization?: {
    cpu_load?: number
    gpu?: Record<string, unknown>
  }
  plan_draft?: PlanDraftSummary | null
  prediction_count: number
  predictions: PredictionSummary[]
  prepared_work_count: number
  prepared_work: PreparedWorkCard[]
  updated_at: number
}

export interface LiveWorkEvent {
  event_id: string
  ts: number
  entity_type: 'prediction' | 'scenario' | 'work_product' | 'worker' | string
  entity_id: string
  stage: string
  status: string
  summary: string
  cycle_id?: string | null
  job_id?: string | null
  scenario_id?: string | null
  targets?: string[]
  model?: string | null
  latency_ms?: number | null
  token_usage?: Record<string, unknown>
  artifact_kind?: string | null
  safe_preview?: string | null
  metadata?: Record<string, unknown>
}

export interface LiveWorkSnapshot {
  entity_type: string
  entity_id: string
  status: string
  summary: string
  active: boolean
  updated_at?: number | null
  events: LiveWorkEvent[]
  prediction?: PredictionSummary | null
  worker?: Record<string, unknown> | null
  queue?: Record<string, unknown> | null
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
