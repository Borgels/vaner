import type {
  ArtefactDetail,
  ActiveWorkPayload,
  BackendPreset,
  BackendSettings,
  ComputeDevice,
  ComputeSettings,
  DecisionRecordPayload,
  ExternalStateDiscovery,
  ExternalStateSettings,
  FocusRoutePayload,
  Goal,
  HeatmapReplayPayload,
  ImpactSummary,
  JobsStatusPayload,
  LearningRecent,
  LimitSettings,
  LiveWorkSnapshot,
  MCPSettings,
  PredictionsByState,
  PreparedWorkAction,
  PreparedWorkCard,
  RecentActivityPayload,
  RecentEventsPayload,
  ScenarioApiPayload,
  ScenarioInspectorPayload,
  SignalCapabilitiesPayload,
  SourcesPermissionsPayload,
  StatusPayload,
  UIPinnedFact,
  UISkill,
  WorkProductInspection,
  Artefact,
} from '../types'

const JSON_HEADERS = { 'content-type': 'application/json' }

export function openEventSource(path: string): EventSource {
  return new EventSource(path)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { headers: { accept: 'application/json', ...(init?.headers || {}) }, ...init })
  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    throw new Error(detail || `HTTP ${response.status}`)
  }
  const contentType = response.headers.get('content-type') ?? ''
  if (!contentType.includes('application/json')) {
    const text = await response.text().catch(() => '')
    const hint = text.trimStart().startsWith('<')
      ? 'The cockpit received HTML instead of daemon JSON. Start Vaner with `vaner up` or use the Vite proxy against the daemon.'
      : `Expected JSON but received ${contentType || 'an unknown content type'}.`
    throw new Error(hint)
  }
  return (await response.json()) as T
}

export function getStatus(): Promise<StatusPayload> {
  return request<StatusPayload>('/status')
}

export function getFocusRoute(): Promise<FocusRoutePayload> {
  return request<FocusRoutePayload>('/focus/route')
}

export function getJobsStatus(): Promise<JobsStatusPayload> {
  return request<JobsStatusPayload>('/jobs')
}

export function getSourcesPermissions(): Promise<SourcesPermissionsPayload> {
  return request<SourcesPermissionsPayload>('/sources/permissions')
}

export function getSignalCapabilities(): Promise<SignalCapabilitiesPayload> {
  return request<SignalCapabilitiesPayload>('/signals/capabilities')
}

export function getRecentActivity(params: { limit?: number; hostApp?: string; source?: string } = {}): Promise<RecentActivityPayload> {
  const query = new URLSearchParams()
  query.set('limit', String(params.limit ?? 12))
  if (params.hostApp) query.set('host_app', params.hostApp)
  if (params.source) query.set('source', params.source)
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), 7000)
  return request<RecentActivityPayload>(`/activity/recent?${query.toString()}`, { signal: controller.signal }).finally(() => {
    window.clearTimeout(timeout)
  })
}

export function getRecentEvents(params: { limit?: number; corpusId?: string } = {}): Promise<RecentEventsPayload> {
  const query = new URLSearchParams()
  query.set('limit', String(params.limit ?? 50))
  if (params.corpusId) query.set('corpus_id', params.corpusId)
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), 7000)
  return request<RecentEventsPayload>(`/events/recent?${query.toString()}`, { signal: controller.signal }).finally(() => {
    window.clearTimeout(timeout)
  })
}

export function getComputeDevices(): Promise<{ devices: ComputeDevice[]; warning?: string | null }> {
  return request('/compute/devices')
}

export function getBackendPresets(): Promise<{ presets: BackendPreset[] }> {
  return request('/backend/presets')
}

export function listSkills(): Promise<{ skills: UISkill[] }> {
  return request('/skills')
}

export function listPinnedFacts(): Promise<{ facts: UIPinnedFact[] }> {
  return request('/pinned-facts')
}

export function listPreparedWork(params: {
  limit?: number
  includeAdvisory?: boolean
  includeDiagnostics?: boolean
  contextId?: string | null
  surface?: 'cockpit' | 'desktop' | 'mcp_app' | 'api'
} = {}): Promise<{ prepared_work: PreparedWorkCard[] }> {
  const query = new URLSearchParams()
  query.set('surface', params.surface ?? 'cockpit')
  if (params.limit) query.set('limit', String(params.limit))
  if (params.includeAdvisory) query.set('include_advisory', 'true')
  if (params.includeDiagnostics) query.set('include_diagnostics', 'true')
  if (params.contextId) query.set('context_id', params.contextId)
  return request(`/prepared-work?${query.toString()}`)
}

export function getImpactSummary(): Promise<ImpactSummary> {
  return request('/impact/summary')
}

export function listDecisions(): Promise<{ items: DecisionRecordPayload[] }> {
  return request('/decisions')
}

export function sendOutcome(id: string, outcome: 'useful' | 'partial' | 'irrelevant'): Promise<{ scenario: ScenarioApiPayload | null }> {
  return request(`/scenarios/${encodeURIComponent(id)}/outcome`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ outcome }),
  })
}

export function expandScenario(id: string): Promise<{ scenario: ScenarioApiPayload | null }> {
  return request(`/scenarios/${encodeURIComponent(id)}/expand`, { method: 'POST' })
}

export function togglePin(id: string, pinned: boolean): Promise<{ scenario: ScenarioApiPayload | null }> {
  return request(`/scenarios/${encodeURIComponent(id)}/pin`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ pinned }),
  })
}

export async function deletePinnedFact(id: string): Promise<void> {
  await togglePin(id, false)
}

export function updateBackend(patch: Partial<BackendSettings>): Promise<{ backend: BackendSettings }> {
  return request('/backend', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(patch) })
}

export function updateCompute(patch: Partial<ComputeSettings>): Promise<{ compute: ComputeSettings }> {
  return request('/compute', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(patch) })
}

export function updateMcp(patch: Partial<MCPSettings>): Promise<{ mcp: MCPSettings }> {
  return request('/mcp', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(patch) })
}

export function getExternalState(): Promise<ExternalStateSettings> {
  return request('/external-state')
}

export function updateExternalState(patch: Partial<Pick<ExternalStateSettings, 'enabled' | 'max_calls_per_cycle' | 'max_cycle_ms'>>): Promise<ExternalStateSettings> {
  return request('/external-state', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(patch) })
}

export function saveExternalProvider(payload: {
  id: string
  transport: 'stdio' | 'streamable_http'
  command?: string
  args?: string[]
  url?: string
  env?: Record<string, string>
  timeout_ms?: number
}): Promise<ExternalStateSettings> {
  return request('/external-state/providers', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(payload) })
}

export function discoverExternalProvider(providerId: string, options: { apply?: boolean; trust_unknown_read?: boolean } = {}): Promise<ExternalStateDiscovery> {
  return request(`/external-state/providers/${encodeURIComponent(providerId)}/discover`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(options),
  })
}

export function updateExternalFinance(patch: Partial<ExternalStateSettings['finance']>): Promise<ExternalStateSettings> {
  return request('/external-state/finance', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(patch) })
}

export function updateContext(max_context_tokens: number): Promise<{ limits: LimitSettings }> {
  return request('/context', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ max_context_tokens }) })
}

export function toggleGateway(enabled: boolean): Promise<{ enabled: boolean }> {
  return request('/gateway/toggle', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ enabled }) })
}

export function runPreparedWorkAction(action: PreparedWorkAction): Promise<unknown> {
  const method = action.kind === 'inspect' ? 'GET' : 'POST'
  const feedbackState =
    typeof action.arguments?.feedback_state === 'string' ? action.arguments.feedback_state : 'useful'
  const body = action.kind === 'feedback' ? JSON.stringify({ feedback_state: feedbackState }) : undefined
  const endpoint = action.endpoint ?? ''
  return request(endpoint, {
    method,
    headers: body ? JSON_HEADERS : undefined,
    body,
  })
}

// --- Cockpit refresh: goals, artefacts, predictions-by-state, learning ---

export function listGoals(params: { status?: string; limit?: number } = {}): Promise<{ goals: Goal[] }> {
  const query = new URLSearchParams()
  if (params.status) query.set('status', params.status)
  if (params.limit) query.set('limit', String(params.limit))
  const qs = query.toString()
  return request(`/goals${qs ? `?${qs}` : ''}`)
}

export function listArtefacts(
  params: { status?: string; connector?: string; sourceTier?: string; limit?: number } = {},
): Promise<{ artefacts: Artefact[] }> {
  const query = new URLSearchParams()
  if (params.status) query.set('status', params.status)
  if (params.connector) query.set('connector', params.connector)
  if (params.sourceTier) query.set('source_tier', params.sourceTier)
  if (params.limit) query.set('limit', String(params.limit))
  const qs = query.toString()
  return request(`/artefacts${qs ? `?${qs}` : ''}`)
}

export function fetchArtefact(id: string): Promise<ArtefactDetail> {
  return request(`/artefacts/${encodeURIComponent(id)}`)
}

export function listPredictionsByState(): Promise<PredictionsByState> {
  return request('/predictions/active?include_all=true')
}

export function getActiveWork(): Promise<ActiveWorkPayload> {
  return request('/work/active')
}

export function getHeatmapReplay(params: { fromTs: number; toTs: number; limit?: number }): Promise<HeatmapReplayPayload> {
  const query = new URLSearchParams()
  query.set('from_ts', String(params.fromTs / 1000))
  query.set('to_ts', String(params.toTs / 1000))
  if (params.limit) query.set('limit', String(params.limit))
  return request(`/heatmap/replay?${query.toString()}`)
}

export function streamHeatmapReplay(params: { rangeMs: number; limit?: number }): EventSource {
  const query = new URLSearchParams()
  query.set('range_seconds', String(params.rangeMs / 1000))
  if (params.limit) query.set('limit', String(params.limit))
  return openEventSource(`/heatmap/replay/stream?${query.toString()}`)
}

export function getLiveWork(entityType: string, entityId: string, limit = 80): Promise<LiveWorkSnapshot> {
  const query = new URLSearchParams()
  query.set('entity_type', entityType)
  query.set('entity_id', entityId)
  query.set('limit', String(limit))
  return request(`/work/live?${query.toString()}`)
}

export function liveWorkStreamPath(entityType: string, entityId: string): string {
  const query = new URLSearchParams()
  query.set('entity_type', entityType)
  query.set('entity_id', entityId)
  return `/work/live/stream?${query.toString()}`
}

export function inspectWorkProduct(id: string): Promise<WorkProductInspection> {
  return request(`/work-products/${encodeURIComponent(id)}/inspect`)
}

export function fetchScenarioDetail(id: string): Promise<ScenarioInspectorPayload> {
  return request(`/scenarios/${encodeURIComponent(id)}`)
}

export function getLearningRecent(limit = 5): Promise<LearningRecent> {
  return request(`/learning/recent?limit=${limit}`)
}
