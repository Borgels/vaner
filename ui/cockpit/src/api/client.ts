import type {
  BackendPreset,
  BackendSettings,
  BootstrapPayload,
  ComputeDevice,
  ComputeSettings,
  DecisionRecordPayload,
  ImpactSummary,
  MCPSettings,
  ScenarioApiPayload,
  StatusPayload,
  UIEvent,
  UIPinnedFact,
  UISkill,
} from '../types'

async function requestJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init)
  if (!response.ok) {
    throw new Error((await response.text()) || `Request failed: ${response.status}`)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

export async function getBootstrap(): Promise<BootstrapPayload> {
  return requestJson('/cockpit/bootstrap.json')
}

export async function getStatus(): Promise<StatusPayload> {
  return requestJson('/status')
}

export async function getComputeDevices(): Promise<{ devices: ComputeDevice[]; selected?: string; warning?: string }> {
  return requestJson('/compute/devices')
}

export async function updateCompute(
  payload: Partial<ComputeSettings>,
): Promise<{ ok: boolean; compute: ComputeSettings }> {
  return requestJson('/compute', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function getBackendPresets(): Promise<{ presets: BackendPreset[] }> {
  return requestJson('/backend/presets')
}

export async function updateBackend(
  payload: Partial<BackendSettings>,
): Promise<{ ok: boolean; backend: BackendSettings }> {
  return requestJson('/backend', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function updateContext(
  max_context_tokens: number,
): Promise<{ ok: boolean; limits: { max_context_tokens: number } }> {
  return requestJson('/context', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ max_context_tokens }),
  })
}

export async function updateMcp(payload: Partial<MCPSettings>): Promise<{ ok: boolean; mcp: MCPSettings }> {
  return requestJson('/mcp', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function nudgeSkill(
  name: string,
  delta: number,
): Promise<{ ok: boolean; name: string; weight: number }> {
  return requestJson(`/skills/${encodeURIComponent(name)}/nudge`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ delta }),
  })
}

export async function listScenarios(limit = 25): Promise<{ count: number; scenarios: ScenarioApiPayload[] }> {
  return requestJson(`/scenarios?limit=${limit}`)
}

export async function getScenario(id: string): Promise<ScenarioApiPayload> {
  return requestJson(`/scenarios/${id}`)
}

export async function expandScenario(id: string): Promise<{ ok: boolean; scenario: ScenarioApiPayload | null }> {
  return requestJson(`/scenarios/${id}/expand`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({}),
  })
}

export async function sendOutcome(
  id: string,
  result: 'useful' | 'partial' | 'irrelevant',
): Promise<{ ok: boolean; scenario: ScenarioApiPayload | null }> {
  return requestJson(`/scenarios/${id}/outcome`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ result }),
  })
}

export async function togglePin(id: string, pinned: boolean): Promise<{ ok: boolean; scenario: ScenarioApiPayload | null }> {
  return requestJson(`/scenarios/${id}/pin`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ pinned }),
  })
}

export async function listPinnedFacts(): Promise<{ facts: UIPinnedFact[] }> {
  return requestJson('/pinned-facts')
}

export async function createPinnedFact(text: string): Promise<{ fact: UIPinnedFact }> {
  return requestJson('/pinned-facts', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ text }),
  })
}

export async function deletePinnedFact(id: string): Promise<void> {
  return requestJson(`/pinned-facts/${id}`, { method: 'DELETE' })
}

export async function listSkills(): Promise<{ skills: UISkill[] }> {
  return requestJson('/skills')
}

export async function getImpactSummary(): Promise<ImpactSummary> {
  return requestJson('/impact/summary')
}

export async function listDecisions(limit = 100): Promise<{ items: DecisionRecordPayload[] }> {
  return requestJson(`/decisions?limit=${limit}`)
}

export async function toggleGateway(enabled: boolean): Promise<{ ok: boolean; gateway_enabled: boolean }> {
  return requestJson('/gateway/toggle', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
}

export function openEventSource(path: string): EventSource {
  return new EventSource(path)
}

export type { UIEvent }
