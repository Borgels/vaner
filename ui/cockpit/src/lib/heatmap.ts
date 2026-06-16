import type { PipelineEvent } from '../api/usePipelineEvents'
import type { LiveWorkEvent, ScenarioHeatmapSample, UIScenario } from '../types'

export type HeatmapEventType = 'work' | 'model' | 'tool' | 'file' | 'test' | 'error' | 'risk' | 'context' | 'suggestion' | 'user'
export type HeatmapGroupBy = 'cluster' | 'status' | 'file-area' | 'type'
export type HeatmapSortBy = 'active' | 'readiness' | 'relevance' | 'confidence' | 'recent'

export interface HeatmapEvent {
  id: string
  timestamp: number
  scenarioId: string | null
  type: HeatmapEventType
  title: string
  description: string
  severity: 'info' | 'warn' | 'error'
  magnitude: number
  paths: string[]
}

export interface HeatmapSample {
  timestamp: number
  intensity: number
  readiness: number
  relevance: number
  confidence: number
  freshness: number
}

export interface HeatmapRow {
  scenario: UIScenario
  status: string
  cluster: string
  active: boolean
  currentIntensity: number
  samples: HeatmapSample[]
  events: HeatmapEvent[]
}

export interface HeatmapBuildOptions {
  now: number
  rangeMs: number
  buckets?: number
  activeScenarioId?: string | null
  showCompleted?: boolean
  showStale?: boolean
  showLowConfidence?: boolean
  onlyPinned?: boolean
  onlyWithEvents?: boolean
  eventTypes?: Set<HeatmapEventType>
  groupBy?: HeatmapGroupBy
  sortBy?: HeatmapSortBy
  samples?: ScenarioHeatmapSample[]
  events?: HeatmapEvent[]
}

export function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value))
}

export function readinessValue(readiness: UIScenario['readiness']): number {
  return ({ ready: 1, warming: 0.62, cooling: 0.3, unprepared: 0.16 } as Record<string, number>)[readiness] ?? 0.35
}

export function freshnessValue(freshness: UIScenario['freshness']): number {
  return ({ fresh: 1, recent: 0.68, stale: 0.16 } as Record<string, number>)[freshness] ?? 0.45
}

export function scenarioStatus(scenario: UIScenario, activeScenarioId?: string | null): string {
  if (scenario.id === activeScenarioId || scenario.decisionState === 'active') return 'active'
  if (scenario.decisionState === 'rejected') return 'rejected'
  if (scenario.visibility === 'archived' || scenario.freshness === 'stale') return 'stale'
  if (scenario.readiness === 'ready') return 'ready'
  if (scenario.readiness === 'warming') return 'prep'
  if (scenario.readiness === 'cooling') return 'cooling'
  return 'prep'
}

export function scenarioIntensity(scenario: UIScenario, activeScenarioId?: string | null): number {
  const relevance = clamp01(Number(scenario.relevance) || 0)
  const readiness = readinessValue(scenario.readiness)
  const confidence = clamp01(Number(scenario.confidence) || 0)
  const freshness = freshnessValue(scenario.freshness)
  const status = scenarioStatus(scenario, activeScenarioId)
  const risk = status === 'rejected' ? 0 : status === 'stale' ? 0.38 : 0.86
  let intensity = relevance * 0.35 + readiness * 0.25 + confidence * 0.2 + freshness * 0.1 + risk * 0.1
  if (scenario.id === activeScenarioId) intensity += 0.08
  if (scenario.pinned) intensity = Math.max(intensity, 0.34)
  if (status === 'stale') intensity *= 0.58
  if (status === 'rejected') intensity *= 0.34
  return clamp01(intensity)
}

export function normalizeHeatmapEvents(events: PipelineEvent[]): HeatmapEvent[] {
  const rows: HeatmapEvent[] = []
  for (const event of events) {
    if (event.kind === 'work.snapshot') {
      const items = Array.isArray(event.payload.items) ? event.payload.items : []
      for (const item of items) {
        if (!isRecord(item)) continue
        const live = item as unknown as LiveWorkEvent
        rows.push({
          id: String(live.event_id || `${event.id}-${rows.length}`),
          timestamp: Number(live.ts || event.ts) * 1000,
          scenarioId: live.scenario_id || (live.entity_type === 'prediction' ? `prediction:${live.entity_id}` : live.entity_type === 'scenario' ? live.entity_id : null),
          type: heatmapEventType(String(live.stage || ''), String(live.status || ''), String(live.summary || '')),
          title: live.summary || `${live.stage} ${live.status}`,
          description: live.safe_preview || live.summary || '',
          severity: live.status === 'error' ? 'error' : live.status === 'blocked' ? 'warn' : 'info',
          magnitude: heatmapMagnitude(String(live.stage || ''), Number(live.latency_ms || 0), live.token_usage),
          paths: Array.isArray(live.targets) ? live.targets.filter((target): target is string => typeof target === 'string') : [],
        })
      }
      continue
    }

    rows.push({
      id: event.id,
      timestamp: event.ts * 1000,
      scenarioId: event.scn,
      type: heatmapEventType(event.stage, event.kind, event.msg),
      title: event.msg || event.kind,
      description: event.path || event.kind,
      severity: event.kind.includes('error') ? 'error' : event.kind.includes('risk') ? 'warn' : 'info',
      magnitude: heatmapMagnitude(event.stage, Number(event.payload.latency_ms || 0), event.payload),
      paths: event.path ? [event.path] : event.payload.paths && Array.isArray(event.payload.paths) ? event.payload.paths.filter((path): path is string => typeof path === 'string') : [],
    })
  }
  return dedupeEvents(rows).sort((a, b) => a.timestamp - b.timestamp)
}

export function normalizeLiveWorkHeatmapEvents(events: LiveWorkEvent[]): HeatmapEvent[] {
  return dedupeEvents(events.map((live, index) => ({
    id: String(live.event_id || `live-${index}`),
    timestamp: Number(live.ts || 0) * 1000,
    scenarioId: live.scenario_id || (live.entity_type === 'prediction' ? `prediction:${live.entity_id}` : live.entity_type === 'scenario' ? live.entity_id : null),
    type: heatmapEventType(String(live.stage || ''), String(live.status || ''), String(live.summary || '')),
    title: live.summary || `${live.stage} ${live.status}`,
    description: live.safe_preview || live.summary || '',
    severity: live.status === 'error' ? 'error' : live.status === 'blocked' ? 'warn' : 'info',
    magnitude: heatmapMagnitude(String(live.stage || ''), Number(live.latency_ms || 0), live.token_usage),
    paths: Array.isArray(live.targets) ? live.targets.filter((target): target is string => typeof target === 'string') : [],
  }))).sort((a, b) => a.timestamp - b.timestamp)
}

export function buildScenarioHeatmapData(scenarios: UIScenario[], pipelineEvents: PipelineEvent[], options: HeatmapBuildOptions): HeatmapRow[] {
  const start = options.now - options.rangeMs
  const events = (options.events ?? normalizeHeatmapEvents(pipelineEvents)).filter((event) => event.timestamp >= start && event.timestamp <= options.now)
  const eventTypes = options.eventTypes
  const filteredEvents = eventTypes ? events.filter((event) => eventTypes.has(event.type)) : events
  const samplesByScenario = samplesByScenarioId(options.samples ?? [], start, options.now)
  const byScenario = new Map<string, HeatmapEvent[]>()
  for (const event of filteredEvents) {
    if (!event.scenarioId) continue
    const current = byScenario.get(event.scenarioId) ?? []
    current.push(event)
    byScenario.set(event.scenarioId, current)
  }
  addEventBackedRows(scenarios, byScenario, samplesByScenario)

  const rows = scenarios
    .filter((scenario) => filterScenario(scenario, byScenario.get(scenario.id) ?? [], options))
    .map((scenario): HeatmapRow => {
      const scenarioEvents = byScenario.get(scenario.id) ?? []
      const samples = samplesByScenario.get(scenario.id) ?? []
      const currentIntensity = samples.length ? samples[samples.length - 1].intensity : scenarioIntensity(scenario, options.activeScenarioId)
      return {
        scenario,
        status: scenarioStatus(scenario, options.activeScenarioId),
        cluster: scenarioCluster(scenario, options.groupBy ?? 'cluster'),
        active: scenario.id === options.activeScenarioId || scenario.decisionState === 'active',
        currentIntensity,
        samples,
        events: scenarioEvents,
      }
    })
  return sortHeatmapRows(rows, options.sortBy ?? 'active')
}

function addEventBackedRows(
  scenarios: UIScenario[],
  byScenario: Map<string, HeatmapEvent[]>,
  samplesByScenario: Map<string, HeatmapSample[]>,
): void {
  const known = new Set(scenarios.map((scenario) => scenario.id))
  for (const [scenarioId, scenarioEvents] of byScenario.entries()) {
    if (known.has(scenarioId) || !scenarioEvents.length) continue
    const latest = scenarioEvents[scenarioEvents.length - 1]
    const first = scenarioEvents[0]
    const confidence = clamp01(Math.max(0.35, ...scenarioEvents.map((event) => event.magnitude)))
    const active = scenarioEvents.some((event) => Date.now() - event.timestamp < 20_000 && event.severity !== 'error')
    scenarios.push({
      id: scenarioId,
      kind: 'research',
      title: scenarioId.startsWith('prediction:') ? `Prediction ${scenarioId.replace('prediction:', '').slice(0, 8)}` : latest.title,
      score: confidence,
      relevance: confidence,
      confidence,
      visiblePriority: confidence,
      freshness: active ? 'fresh' : 'recent',
      readiness: active ? 'warming' : 'ready',
      visibility: active ? 'prominent' : 'warming',
      lifecycleMotion: active ? 'rising' : 'stable',
      createdAt: first.timestamp / 1000,
      lastRefreshedAt: latest.timestamp / 1000,
      lastReinforcedAt: latest.timestamp / 1000,
      archivedAt: null,
      visibilityReason: 'Real live-work events without a stored scenario id.',
      depth: 1,
      parent: scenarioId.startsWith('prediction:') ? 'live-work' : null,
      path: latest.paths[0] ?? '',
      skill: null,
      decisionState: active ? 'active' : 'idle',
      reason: latest.description || latest.title,
      entities: latest.paths,
      pinned: false,
    })
    samplesByScenario.set(
      scenarioId,
      scenarioEvents.map((event) => ({
        timestamp: event.timestamp,
        intensity: clamp01(0.28 + event.magnitude * 0.64),
        readiness: active ? 0.62 : 1,
        relevance: clamp01(0.35 + event.magnitude * 0.55),
        confidence,
        freshness: active ? 1 : 0.68,
      })),
    )
    known.add(scenarioId)
  }
}

export function scenarioStateAtCursor(row: HeatmapRow, cursorMs: number): HeatmapSample {
  if (!row.samples.length) {
    return {
      timestamp: cursorMs,
      intensity: row.currentIntensity,
      readiness: readinessValue(row.scenario.readiness),
      relevance: row.scenario.relevance,
      confidence: row.scenario.confidence,
      freshness: freshnessValue(row.scenario.freshness),
    }
  }
  let best = row.samples[0]
  for (const sample of row.samples) {
    if (sample.timestamp <= cursorMs) {
      best = sample
    } else {
      break
    }
  }
  return best ?? row.samples[0]
}

export function filterEventsByType(events: HeatmapEvent[], enabled: Set<HeatmapEventType>): HeatmapEvent[] {
  return events.filter((event) => enabled.has(event.type))
}

export function sortHeatmapRows(rows: HeatmapRow[], sortBy: HeatmapSortBy): HeatmapRow[] {
  return [...rows].sort((a, b) => {
    if (sortBy === 'active') {
      const activeDelta = Number(b.active) - Number(a.active)
      if (activeDelta) return activeDelta
      const eventDelta = latestEventTs(b) - latestEventTs(a)
      if (eventDelta) return eventDelta
      return b.currentIntensity - a.currentIntensity
    }
    if (sortBy === 'readiness') return readinessValue(b.scenario.readiness) - readinessValue(a.scenario.readiness)
    if (sortBy === 'relevance') return b.scenario.relevance - a.scenario.relevance
    if (sortBy === 'confidence') return b.scenario.confidence - a.scenario.confidence
    return latestEventTs(b) - latestEventTs(a)
  })
}

export function scenarioCluster(scenario: UIScenario, groupBy: HeatmapGroupBy): string {
  if (groupBy === 'status') return scenarioStatus(scenario)
  if (groupBy === 'type') return scenario.kind
  if (groupBy === 'file-area') return scenario.path.split('/').slice(0, 2).join('/') || 'workspace'
  return scenario.parent ?? scenario.kind
}

function sampleAt(
  scenario: UIScenario,
  events: HeatmapEvent[],
  timestamp: number,
  start: number,
  now: number,
  bucketMs: number,
  currentIntensity: number,
): HeatmapSample {
  const reinforcedAt = Number(scenario.lastReinforcedAt || scenario.lastRefreshedAt || scenario.createdAt || now) * 1000
  const rangeProgress = clamp01((timestamp - start) / Math.max(1, now - start))
  const warmup = timestamp < reinforcedAt ? 0.28 + rangeProgress * 0.48 : 0.82 + rangeProgress * 0.18
  let intensity = currentIntensity * warmup
  for (const event of events) {
    const distance = Math.abs(event.timestamp - timestamp)
    if (distance > bucketMs * 3) continue
    intensity += event.magnitude * 0.18 * Math.exp(-distance / Math.max(1, bucketMs * 1.2))
  }
  return {
    timestamp,
    intensity: clamp01(intensity),
    readiness: readinessValue(scenario.readiness),
    relevance: scenario.relevance,
    confidence: scenario.confidence,
    freshness: freshnessValue(scenario.freshness),
  }
}

function samplesByScenarioId(samples: ScenarioHeatmapSample[], start: number, end: number): Map<string, HeatmapSample[]> {
  const grouped = new Map<string, HeatmapSample[]>()
  for (const sample of samples) {
    const timestamp = Number(sample.ts || 0) * 1000
    if (timestamp < start || timestamp > end) continue
    const row: HeatmapSample = {
      timestamp,
      intensity: sampleIntensity(sample),
      readiness: readinessValue(sample.readiness as UIScenario['readiness']),
      relevance: clamp01(Number(sample.relevance) || 0),
      confidence: clamp01(Number(sample.confidence) || 0),
      freshness: freshnessValue(sample.freshness as UIScenario['freshness']),
    }
    const current = grouped.get(sample.scenario_id) ?? []
    current.push(row)
    grouped.set(sample.scenario_id, current)
  }
  for (const rows of grouped.values()) {
    rows.sort((a, b) => a.timestamp - b.timestamp)
  }
  return grouped
}

function sampleIntensity(sample: ScenarioHeatmapSample): number {
  const readiness = readinessValue(sample.readiness as UIScenario['readiness'])
  const freshness = freshnessValue(sample.freshness as UIScenario['freshness'])
  const relevance = clamp01(Number(sample.relevance) || 0)
  const confidence = clamp01(Number(sample.confidence) || 0)
  const risk = sample.status === 'rejected' ? 0 : sample.status === 'stale' ? 0.38 : 0.86
  let intensity = relevance * 0.35 + readiness * 0.25 + confidence * 0.2 + freshness * 0.1 + risk * 0.1
  if (sample.active) intensity += 0.08
  if (sample.pinned) intensity = Math.max(intensity, 0.34)
  if (sample.status === 'stale') intensity *= 0.58
  if (sample.status === 'rejected') intensity *= 0.34
  return clamp01(intensity)
}

function filterScenario(scenario: UIScenario, events: HeatmapEvent[], options: HeatmapBuildOptions): boolean {
  const status = scenarioStatus(scenario, options.activeScenarioId)
  if (options.onlyPinned && !scenario.pinned) return false
  if (options.onlyWithEvents && !events.length) return false
  if (options.showCompleted === false && status === 'completed') return false
  if (options.showStale === false && status === 'stale') return false
  if (options.showLowConfidence === false && scenario.confidence < 0.45) return false
  return true
}

function heatmapEventType(stage: string, kind: string, text = ''): HeatmapEventType {
  const value = `${stage} ${kind} ${text}`.toLowerCase()
  if (value.includes('error') || value.includes('failed')) return 'error'
  if (value.includes('risk') || value.includes('blocked')) return 'risk'
  if (value.includes('test')) return 'test'
  if (value.includes('file') || value.includes('artefact')) return 'file'
  if (value.includes('context') || value.includes('briefing')) return 'context'
  if (value.includes('suggest') || value.includes('prediction')) return 'suggestion'
  if (value.includes('signal') || value.includes('user') || value.includes('codex')) return 'user'
  if (value.includes('model') || value.includes('llm')) return 'model'
  if (value.includes('tool')) return 'tool'
  return 'work'
}

function heatmapMagnitude(stage: string, latencyMs: number, usage: unknown): number {
  const tokenUsage = isRecord(usage) && isRecord(usage.token_usage) ? usage.token_usage : usage
  const totalTokens = isRecord(tokenUsage) ? Number(tokenUsage.total_tokens || 0) : 0
  const modelBoost = stage.includes('model') ? 0.18 : 0
  return clamp01(0.32 + modelBoost + Math.min(0.26, latencyMs / 12000) + Math.min(0.24, totalTokens / 10000))
}

function dedupeEvents(events: HeatmapEvent[]): HeatmapEvent[] {
  const seen = new Set<string>()
  const out: HeatmapEvent[] = []
  for (const event of events) {
    if (seen.has(event.id)) continue
    seen.add(event.id)
    out.push(event)
  }
  return out
}

function latestEventTs(row: HeatmapRow): number {
  return row.events.reduce((latest, event) => Math.max(latest, event.timestamp), 0)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
