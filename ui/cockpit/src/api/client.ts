import type {
  ArtefactDetail,
  BackendPreset,
  BackendSettings,
  ComputeDevice,
  ComputeSettings,
  DecisionRecordPayload,
  Goal,
  ImpactSummary,
  LearningRecent,
  LimitSettings,
  MCPSettings,
  PredictionsByState,
  PreparedWorkCard,
  ScenarioApiPayload,
  ScenarioInspectorPayload,
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
  const response = await fetch(path, init)
  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    throw new Error(detail || `HTTP ${response.status}`)
  }
  return (await response.json()) as T
}

export function getStatus(): Promise<StatusPayload> {
  return request<StatusPayload>('/status')
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
  return request(`/scenarios/${encodeURIComponent(id)}/outcome`, {
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

export function updateContext(max_context_tokens: number): Promise<{ limits: LimitSettings }> {
  return request('/context', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ max_context_tokens }) })
}

export function toggleGateway(enabled: boolean): Promise<{ enabled: boolean }> {
  return request('/gateway/toggle', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ enabled }) })
}

export function runPreparedWorkAction(endpoint: string, kind: string): Promise<unknown> {
  const method = kind === 'inspect' ? 'GET' : 'POST'
  const body = kind === 'feedback' ? JSON.stringify({ feedback_state: 'useful' }) : undefined
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

export function inspectWorkProduct(id: string): Promise<WorkProductInspection> {
  return request(`/work-products/${encodeURIComponent(id)}/inspect`)
}

export function fetchScenarioDetail(id: string): Promise<ScenarioInspectorPayload> {
  return request(`/scenarios/${encodeURIComponent(id)}`)
}

export function getLearningRecent(limit = 5): Promise<LearningRecent> {
  return request(`/learning/recent?limit=${limit}`)
}
