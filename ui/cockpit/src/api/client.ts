import type {
  BackendPreset,
  BackendSettings,
  ComputeDevice,
  ComputeSettings,
  DecisionRecordPayload,
  ImpactSummary,
  LimitSettings,
  MCPSettings,
  ScenarioApiPayload,
  StatusPayload,
  UIPinnedFact,
  UISkill,
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

async function optional<T>(path: string, fallback: T): Promise<T> {
  try {
    return await request<T>(path)
  } catch {
    return fallback
  }
}

export function getStatus(): Promise<StatusPayload> {
  return request<StatusPayload>('/status')
}

export function getComputeDevices(): Promise<{ devices: ComputeDevice[]; warning?: string | null }> {
  return optional('/compute/devices', { devices: [] })
}

export function getBackendPresets(): Promise<{ presets: BackendPreset[] }> {
  return optional('/backend/presets', { presets: [] })
}

export function listSkills(): Promise<{ skills: UISkill[] }> {
  return optional('/skills', { skills: [] })
}

export function listPinnedFacts(): Promise<{ facts: UIPinnedFact[] }> {
  return optional('/pinned-facts', { facts: [] })
}

export function getImpactSummary(): Promise<ImpactSummary> {
  return optional('/impact/summary', { count: 0 })
}

export function listDecisions(): Promise<{ items: DecisionRecordPayload[] }> {
  return optional('/decisions', { items: [] })
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

export async function deletePinnedFact(_id: string): Promise<void> {
  return undefined
}

export async function nudgeSkill(name: string, delta: number): Promise<{ name: string; weight: number }> {
  return { name, weight: Math.max(0, Math.min(1, 0.5 + delta)) }
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
