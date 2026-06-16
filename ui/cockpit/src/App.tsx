import { useCallback, useEffect, useMemo, useState } from 'react'

import { adaptEvidence, adaptScenario } from './api/adapt'
import {
  deletePinnedFact,
  discoverExternalProvider,
  expandScenario,
  getBackendPresets,
  getComputeDevices,
  getExternalState,
  getImpactSummary,
  getSignalCapabilities,
  getSourcesPermissions,
  getStatus,
  listDecisions,
  listPinnedFacts,
  listPredictionsByState,
  listSkills,
  sendOutcome,
  toggleGateway,
  togglePin as toggleScenarioPin,
  updateBackend,
  updateCompute,
  updateContext,
  updateExternalFinance,
  updateExternalState,
  updateMcp,
  saveExternalProvider,
} from './api/client'
import { useBootstrap } from './api/useBootstrap'
import { useActiveWork } from './api/useActiveWork'
import { useEvents } from './api/useEvents'
import { useFocusRuntime } from './api/useFocusRuntime'
import { useLiveWork, type LiveWorkSelection } from './api/useLiveWork'
import { usePipelineEvents, type PipelineEvent } from './api/usePipelineEvents'
import { usePreparedWork } from './api/usePreparedWork'
import { useScenarios } from './api/useScenarios'
import {
  BoardView,
  EvidenceView,
  FocusView,
  NowView,
  OperatorOverviewView,
  PreparedWorkView,
  TimelineView,
} from './components/CockpitViews'
import type { ScoreComponent } from './components/Inspector'
import {
  CommandPalette,
  LeftRail,
  MismatchBanner,
  SettingsDrawer,
  TopBar,
  type CockpitView,
  type CommandItem,
} from './components/chrome'
import { EventStreamPanel } from './components/EventStreamPanel'
import { HeatmapReplayView } from './components/HeatmapReplayView'
import { Inspector } from './components/Inspector'
import { LearningPanel } from './components/LearningPanel'
import { LiveWorkInspector } from './components/LiveWorkInspector'
import { PipelineCanvas } from './components/PipelineCanvas'
import { SystemVitals } from './components/SystemVitals'
import { ACCENT_MAP, DEFAULT_COCKPIT_SETTINGS, KIND_COLOR } from './lib/constants'
import type {
  BackendPreset,
  BackendSettings,
  CockpitSettings,
  ComputeDevice,
  ComputeSettings,
  DecisionRecordPayload,
  ExternalStateDiscovery,
  ExternalStateSettings,
  ImpactSummary,
  LatestInvalidationSignal,
  LimitSettings,
  LiveWorkEvent,
  MCPSettings,
  PredictionsByState,
  PredictionSummary,
  ScenarioApiPayload,
  SignalCapabilitiesPayload,
  SourcesPermissionsPayload,
  StatusPayload,
  UIEvent,
  UIScenario,
  UIScenarioKind,
  UIPackageState,
  UIPinnedFact,
  UISkill,
} from './types'
import { fetchScenarioDetail } from './api/client'

const COCKPIT_BUILD_SHA = (import.meta as unknown as { env?: { VITE_COCKPIT_SHA?: string } }).env?.VITE_COCKPIT_SHA ?? ''

type ScenarioScope = 'live' | 'session' | 'focus' | 'history'

const PREDICTION_STATE_ORDER = ['ready', 'drafting', 'evidence_gathering', 'grounding', 'queued', 'stale']
const LIVE_STALE_GRACE_SECONDS = 30 * 60

function compactWorkspacePath(path: string): string {
  const parts = path.split('/').filter(Boolean)
  return parts.slice(-1)[0] ?? path
}

function predictionReadiness(prediction: PredictionSummary): string {
  return String(prediction.readiness ?? prediction.run?.readiness ?? 'queued')
}

function predictionSource(prediction: PredictionSummary): string {
  return String(prediction.spec?.source ?? prediction.source_label ?? '').toLowerCase()
}

function predictionTitle(prediction: PredictionSummary): string {
  return prediction.display_label ?? prediction.title ?? prediction.label ?? prediction.prompt ?? prediction.spec?.label ?? prediction.id
}

function predictionTargets(prediction: PredictionSummary): string[] {
  const targets = prediction.evidence_targets ?? prediction.watched_sources ?? prediction.spec?.structured?.evidence_targets ?? []
  return targets.filter((target): target is string => typeof target === 'string' && target.length > 0)
}

function isHorizonPrediction(prediction: PredictionSummary): boolean {
  return predictionSource(prediction).includes('horizon') || String(prediction.source_label ?? '').toLowerCase().includes('possible next work')
}

function predictionSortRank(prediction: PredictionSummary): number {
  const state = predictionReadiness(prediction)
  const stateRank = PREDICTION_STATE_ORDER.includes(state) ? PREDICTION_STATE_ORDER.indexOf(state) : PREDICTION_STATE_ORDER.length
  if (isHorizonPrediction(prediction) && state !== 'stale') {
    return state === 'ready' || state === 'drafting' ? -2 : -1
  }
  return stateRank
}

function isReviewablePreparedCard(card: { kind: string; source_type: string; title: string; summary: string; target_label: string; external_input_count?: number }): boolean {
  const text = `${card.kind} ${card.title} ${card.summary} ${card.target_label}`.toLowerCase()
  if (card.kind === 'finance' || text.includes('trading') || text.includes('option') || text.includes('position')) return true
  if (card.source_type === 'work_product') return true
  if (card.kind === 'diff' || card.kind === 'review') return true
  return Number(card.external_input_count ?? 0) > 0
}

function normalizePrediction(prediction: PredictionSummary): PredictionSummary {
  return {
    ...prediction,
    title: prediction.title ?? prediction.display_label ?? prediction.label ?? prediction.spec?.label ?? prediction.prompt ?? prediction.id,
    readiness: prediction.readiness ?? prediction.run?.readiness,
    confidence: prediction.confidence ?? prediction.spec?.confidence,
    updated_at: prediction.updated_at ?? prediction.run?.updated_at,
  }
}

function flattenPredictions(payload: PredictionsByState): PredictionSummary[] {
  const byId = new Map<string, PredictionSummary>()
  const add = (prediction: PredictionSummary) => {
    const key = prediction.id || prediction.prediction_id
    if (!key || byId.has(key)) return
    byId.set(key, normalizePrediction(prediction))
  }

  ;(payload.predictions ?? []).forEach(add)
  for (const state of PREDICTION_STATE_ORDER) {
    ;(payload.by_state?.[state] ?? []).forEach(add)
  }
  Object.entries(payload.by_state ?? {})
    .filter(([state]) => !PREDICTION_STATE_ORDER.includes(state))
    .forEach(([, group]) => group.forEach(add))

  return [...byId.values()].sort((a, b) => {
    const lhsRank = predictionSortRank(a)
    const rhsRank = predictionSortRank(b)
    if (lhsRank !== rhsRank) return lhsRank - rhsRank
    return (b.confidence ?? 0) - (a.confidence ?? 0)
  })
}

function predictionKind(prediction: PredictionSummary): UIScenarioKind {
  const text = `${predictionTitle(prediction)} ${prediction.ui_summary ?? ''} ${prediction.spec?.description ?? ''}`.toLowerCase()
  if (text.includes('refactor')) return 'refactor'
  if (/(security|codeql|test|validate|harden|failure|risk|audit|check)/i.test(text)) return 'debug'
  if (/(document|docs|summary|explain|decision)/i.test(text)) return 'explain'
  if (/(research|explore|compare|investigate)/i.test(text)) return 'research'
  return 'change'
}

function predictionReason(prediction: PredictionSummary): string {
  const matchReason = prediction.match_reason ?? ''
  if (matchReason && !matchReason.toLowerCase().startsWith('no current-turn')) {
    return matchReason
  }
  return prediction.ui_summary ?? prediction.spec?.description ?? 'Vaner is preparing this as likely next work.'
}

function scenarioFromPrediction(prediction: PredictionSummary): UIScenario {
  const targets = predictionTargets(prediction)
  const readiness = predictionReadiness(prediction)
  const confidence = prediction.confidence ?? prediction.spec?.confidence ?? 0.55
  const ready = readiness === 'ready' || readiness === 'drafting'
  return {
    id: `prediction:${prediction.id || prediction.prediction_id}`,
    kind: predictionKind(prediction),
    title: predictionTitle(prediction),
    score: confidence,
    relevance: ready ? Math.max(0.82, confidence) : Math.max(0.58, confidence * 0.9),
    confidence,
    visiblePriority: ready ? Math.max(0.82, confidence) : Math.max(0.58, confidence * 0.9),
    freshness: readiness === 'stale' ? 'stale' : ready ? 'fresh' : 'recent',
    readiness: ready ? 'ready' : readiness === 'stale' ? 'cooling' : 'warming',
    visibility: ready ? 'prominent' : readiness === 'stale' ? 'cooling' : 'warming',
    lifecycleMotion: ready ? 'rising' : readiness === 'stale' ? 'falling' : 'stable',
    createdAt: prediction.created_at ?? null,
    lastRefreshedAt: prediction.updated_at ?? prediction.created_at ?? null,
    lastReinforcedAt: prediction.updated_at ?? prediction.created_at ?? null,
    archivedAt: null,
    visibilityReason: ready ? 'prepared prediction is ready' : 'background prediction is warming',
    depth: 0,
    parent: null,
    path: targets[0] ?? 'background preparation',
    skill: null,
    decisionState: prediction.recommended_action === 'adopt' ? 'chosen' : 'pending',
    reason: predictionReason(prediction),
    entities: targets.slice(0, 10),
    pinned: false,
  }
}

function isPredictionScenarioId(id: string): boolean {
  return id.startsWith('prediction:')
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function liveWorkEventsFromPipeline(events: PipelineEvent[]): LiveWorkEvent[] {
  const rows: LiveWorkEvent[] = []
  for (const event of events) {
    if (event.kind !== 'work.snapshot') {
      continue
    }
    const items = Array.isArray(event.payload.items) ? event.payload.items : []
    for (const item of items) {
      if (!isRecord(item)) {
        continue
      }
      const entityType = String(item.entity_type ?? '')
      const entityId = String(item.entity_id ?? '')
      const eventId = String(item.event_id ?? '')
      if (!entityType || !entityId || !eventId) {
        continue
      }
      rows.push(item as unknown as LiveWorkEvent)
    }
  }
  return rows.sort((a, b) => Number(b.ts ?? 0) - Number(a.ts ?? 0))
}

function isActiveLiveWorkEvent(event: LiveWorkEvent): boolean {
  return (
    ['queued', 'running', 'grounding', 'evidence_gathering', 'drafting'].includes(event.status) ||
    ['model', 'progress', 'prediction_precompute', 'queued'].includes(event.stage)
  )
}

function scenarioCandidatesForLiveWork(event: LiveWorkEvent): string[] {
  const candidates: string[] = []
  if (event.entity_type === 'prediction' && event.entity_id) {
    candidates.push(`prediction:${event.entity_id}`)
  }
  if (event.entity_type === 'scenario' && event.entity_id) {
    candidates.push(event.entity_id)
  }
  if (event.scenario_id) {
    candidates.push(event.scenario_id)
  }
  return candidates
}

function predictionScenarioIdFromLiveWork(event: LiveWorkEvent): string | null {
  return event.entity_type === 'prediction' && event.entity_id ? `prediction:${event.entity_id}` : null
}

function activeScenarioIdFromLiveWork(events: PipelineEvent[], scenarios: UIScenario[]): string | null {
  const scenarioIds = new Set(scenarios.map((scenario) => scenario.id))
  const workEvents = liveWorkEventsFromPipeline(events)
  const activeEvents = workEvents.filter(isActiveLiveWorkEvent)
  for (const event of activeEvents.length ? activeEvents : workEvents) {
    const candidates = event.scenario_id ? [event.scenario_id, ...scenarioCandidatesForLiveWork(event)] : scenarioCandidatesForLiveWork(event)
    for (const candidate of candidates) {
      if (scenarioIds.has(candidate)) {
        return candidate
      }
    }
  }
  return null
}

function activeScenarioCandidateFromPipelineEvent(event: PipelineEvent | undefined): string | null {
  if (!event) {
    return null
  }
  if (event.scn) {
    return event.scn
  }
  const activeEvent = liveWorkEventsFromPipeline([event]).find(isActiveLiveWorkEvent)
  return activeEvent ? activeEvent.scenario_id ?? scenarioCandidatesForLiveWork(activeEvent)[0] ?? null : null
}

function parentScenarioFromLiveWorkEvent(event: LiveWorkEvent): UIScenario | null {
  const id = predictionScenarioIdFromLiveWork(event) ?? (event.entity_type === 'scenario' ? event.entity_id : null)
  if (!id) return null
  const targets = Array.isArray(event.targets) ? event.targets.filter((target): target is string => typeof target === 'string' && target.length > 0) : []
  const active = isActiveLiveWorkEvent(event)
  const summary = event.summary || `${event.entity_type} ${event.entity_id}`
  return {
    id,
    kind: event.stage === 'model' ? 'research' : 'change',
    title: active ? `Working: ${summary}` : summary,
    score: active ? 0.98 : 0.78,
    relevance: active ? 0.98 : 0.76,
    confidence: active ? 0.92 : 0.7,
    visiblePriority: active ? 0.99 : 0.76,
    freshness: active ? 'fresh' : 'recent',
    readiness: active ? 'warming' : 'ready',
    visibility: active ? 'prominent' : 'warming',
    lifecycleMotion: active ? 'rising' : 'stable',
    createdAt: Number(event.ts ?? Date.now() / 1000),
    lastRefreshedAt: Number(event.ts ?? Date.now() / 1000),
    lastReinforcedAt: Number(event.ts ?? Date.now() / 1000),
    archivedAt: null,
    visibilityReason: active ? 'active background work' : 'recent background work',
    depth: 0,
    parent: null,
    path: targets[0] ?? event.scenario_id ?? event.entity_id,
    skill: null,
    decisionState: active ? 'active' : 'pending',
    reason: summary,
    entities: targets.slice(0, 10),
    pinned: active,
  }
}

function childScenarioFromLiveWorkEvent(event: LiveWorkEvent): UIScenario | null {
  if (!event.scenario_id || event.scenario_id === event.entity_id) {
    return null
  }
  const parent = predictionScenarioIdFromLiveWork(event)
  const targets = Array.isArray(event.targets) ? event.targets.filter((target): target is string => typeof target === 'string' && target.length > 0) : []
  const active = isActiveLiveWorkEvent(event)
  return {
    id: event.scenario_id,
    kind: event.stage === 'model' ? 'research' : 'change',
    title: targets[0] ? `Exploring ${targets[0]}` : event.summary || `Scenario ${event.scenario_id.slice(0, 8)}`,
    score: active ? 0.94 : 0.7,
    relevance: active ? 0.92 : 0.68,
    confidence: active ? 0.82 : 0.65,
    visiblePriority: active ? 0.92 : 0.68,
    freshness: active ? 'fresh' : 'recent',
    readiness: active ? 'warming' : 'ready',
    visibility: active ? 'prominent' : 'warming',
    lifecycleMotion: active ? 'rising' : 'stable',
    createdAt: Number(event.ts ?? Date.now() / 1000),
    lastRefreshedAt: Number(event.ts ?? Date.now() / 1000),
    lastReinforcedAt: Number(event.ts ?? Date.now() / 1000),
    archivedAt: null,
    visibilityReason: active ? 'active scenario under prepared work' : 'recent scenario under prepared work',
    depth: parent ? 1 : 0,
    parent,
    path: targets[0] ?? event.scenario_id,
    skill: null,
    decisionState: active ? 'active' : 'pending',
    reason: event.summary,
    entities: targets.slice(0, 10),
    pinned: false,
  }
}

function scenariosFromLiveWorkEvent(event: LiveWorkEvent): UIScenario[] {
  return [parentScenarioFromLiveWorkEvent(event), childScenarioFromLiveWorkEvent(event)].filter((scenario): scenario is UIScenario => Boolean(scenario))
}

function scenarioBelongsOnActiveMap(scenario: UIScenario, nowSeconds = Date.now() / 1000): boolean {
  if (scenario.pinned || scenario.visibility === 'prominent' || scenario.freshness !== 'stale') {
    return true
  }
  const reinforcedAt = Number(scenario.lastReinforcedAt ?? 0)
  return reinforcedAt > 0 && nowSeconds - reinforcedAt <= LIVE_STALE_GRACE_SECONDS
}

function formatDecisionTime(assembledAt: number): string {
  return new Date(assembledAt * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function adaptDecisionEvent(record: DecisionRecordPayload): UIEvent {
  return {
    id: `decision-${record.id}`,
    t: formatDecisionTime(record.assembled_at),
    tag: 'decision',
    color: 'var(--accent)',
    msg: `${record.id} · ${record.selection_count ?? record.selections.length} selections · ${record.token_used}/${record.token_budget} tok`,
    scn: record.id,
  }
}

function proxyPackageFromDecision(decision: DecisionRecordPayload | null): UIPackageState | null {
  if (!decision) {
    return null
  }
  const chosen = decision.selections.filter((selection) => selection.kept).length
  const rejected = decision.selections.filter((selection) => !selection.kept).length
  return {
    id: decision.id,
    tokens: decision.token_used,
    budget: decision.token_budget,
    chosen,
    partial: 0,
    rejected,
    compression: 0,
  }
}

function App() {
  const bootstrap = useBootstrap()
  const pipeline = usePipelineEvents({ path: '/events/stream' })
  const activeWork = useActiveWork(2500)
  const preparedWork = usePreparedWork(24)
  const focusRuntime = useFocusRuntime(3500)
  const [cockpit, setCockpit] = useState<CockpitSettings>(DEFAULT_COCKPIT_SETTINGS)
  const [query, setQuery] = useState('')
  const [scenarioScope, setScenarioScope] = useState<ScenarioScope>('live')
  const scenarioResult = useScenarios(cockpit.topK, pipeline.events, scenarioScope === 'history' ? 'history' : 'live')
  const { setScenarios, scenarioMap, setScenarioMap } = scenarioResult
  const [backend, setBackend] = useState<BackendSettings | null>(null)
  const [compute, setCompute] = useState<ComputeSettings | null>(null)
  const [mcp, setMcp] = useState<MCPSettings | null>(null)
  const [externalState, setExternalState] = useState<ExternalStateSettings | null>(null)
  const [externalStateDiscovery, setExternalStateDiscovery] = useState<ExternalStateDiscovery | null>(null)
  const [limits, setLimits] = useState<LimitSettings | null>(null)
  const [presets, setPresets] = useState<BackendPreset[]>([])
  const [devices, setDevices] = useState<ComputeDevice[]>([])
  const [devicesWarning, setDevicesWarning] = useState<string | null>(null)
  const [gatewayEnabled, setGatewayEnabled] = useState<boolean>(false)
  const [skills, setSkills] = useState<UISkill[]>([])
  const [pinnedFacts, setPinnedFacts] = useState<UIPinnedFact[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [liveSelection, setLiveSelection] = useState<LiveWorkSelection | null>(null)
  const [scenarioAutoFocus, setScenarioAutoFocus] = useState(false)
  const [selectedDecisionId, setSelectedDecisionId] = useState<string | null>(null)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [view, setView] = useState<CockpitView>('focus')
  const [selectedWorkId, setSelectedWorkId] = useState<string | null>(null)
  // Cockpit refresh: lazy-loaded "stale because" reasons keyed by scenario
  // id. We only fetch on selection (and only for non-fresh rows) to avoid
  // a second round-trip for every list-time render.
  const [invalidationById, setInvalidationById] = useState<
    Record<string, LatestInvalidationSignal | null>
  >({})
  const [toast, setToast] = useState<{ msg: string; color: string } | null>(null)
  const [impact, setImpact] = useState<ImpactSummary>({ count: 0 })
  const [decisions, setDecisions] = useState<DecisionRecordPayload[]>([])
  const [activePulses, setActivePulses] = useState<Set<string>>(new Set())
  const [bundleMismatch, setBundleMismatch] = useState(false)
  const [predictionMetrics, setPredictionMetrics] = useState<StatusPayload['prediction_metrics'] | null>(null)
  const [predictionCalibration, setPredictionCalibration] = useState<StatusPayload['prediction_calibration'] | null>(null)
  const [predictions, setPredictions] = useState<PredictionSummary[]>([])
  const [focusState, setFocusState] = useState<StatusPayload['focus'] | null>(null)
  const [sourcesPermissions, setSourcesPermissions] = useState<SourcesPermissionsPayload | null>(null)
  const [signalCapabilities, setSignalCapabilities] = useState<SignalCapabilitiesPayload | null>(null)
  const liveWork = useLiveWork(liveSelection)

  const bootstrapPayload = bootstrap.payload
  const mode = bootstrapPayload?.mode ?? 'daemon'
  const focusMatches =
    !bootstrapPayload?.workspace_id ||
    !focusState?.active_workspace_id ||
    focusState.active_workspace_id === bootstrapPayload.workspace_id
  const livePlanScenario = useMemo<UIScenario | null>(() => {
    const draft = activeWork.snapshot?.plan_draft
    if (!draft) return null
    return {
      id: draft.id,
      kind: 'change',
      title: draft.title,
      score: 0.99,
      relevance: 0.99,
      confidence: 0.96,
      visiblePriority: 0.99,
      freshness: 'fresh',
      readiness: 'ready',
      visibility: 'prominent',
      lifecycleMotion: 'rising',
      createdAt: draft.created_at ?? null,
      lastRefreshedAt: draft.updated_at ?? draft.created_at ?? null,
      lastReinforcedAt: Date.now() / 1000,
      archivedAt: null,
      visibilityReason: 'active draft plan',
      depth: 0,
      parent: null,
      path: draft.prep_dir || 'plan draft',
      skill: null,
      decisionState: 'pending',
      reason: draft.summary || 'Vaner is preparing against this draft plan.',
      entities: draft.tasks?.slice(0, 6) ?? [],
      pinned: true,
    }
  }, [activeWork.snapshot?.plan_draft])

  const predictionScenarios = useMemo<UIScenario[]>(
    () =>
      predictions
        .filter((prediction) => prediction.trust_status !== 'invalidated')
        .filter((prediction) => isHorizonPrediction(prediction))
        .filter((prediction) => predictionReadiness(prediction) !== 'stale')
        .slice(0, 10)
        .map(scenarioFromPrediction),
    [predictions],
  )

  const liveWorkScenarios = useMemo<UIScenario[]>(() => {
    const byId = new Map<string, UIScenario>()
    for (const event of liveWorkEventsFromPipeline(pipeline.events).filter(isActiveLiveWorkEvent).slice(0, 8)) {
      for (const scenario of scenariosFromLiveWorkEvent(event)) {
        if (!byId.has(scenario.id)) {
          byId.set(scenario.id, scenario)
        }
      }
    }
    return [...byId.values()]
  }, [pipeline.events])

  const scenarios = useMemo(() => {
    const withLive = (rows: UIScenario[]) => {
      if (scenarioScope === 'history') {
        return rows
      }
      const activeRows = scenarioScope === 'live' || scenarioScope === 'focus'
        ? rows.filter((row) => scenarioBelongsOnActiveMap(row))
        : rows
      const seeded = [...predictionScenarios, ...liveWorkScenarios.filter((live) => !predictionScenarios.some((prediction) => prediction.id === live.id))]
      const combined = [...seeded, ...activeRows.filter((row) => !seeded.some((seed) => seed.id === row.id))]
      return livePlanScenario ? [livePlanScenario, ...combined.filter((row) => row.id !== livePlanScenario.id)] : combined
    }
    if (scenarioScope === 'focus' && !focusMatches) {
      return []
    }
    if (scenarioScope !== 'session' || !bootstrapPayload?.daemon_started_at) {
      return withLive(scenarioResult.scenarios)
    }
    return withLive(scenarioResult.scenarios.filter((scenario) => {
      if (scenario.freshness === 'stale') {
        return false
      }
      const refreshedAt = scenarioMap[scenario.id]?.last_refreshed_at ?? scenarioMap[scenario.id]?.created_at
      return typeof refreshedAt === 'number' && refreshedAt >= bootstrapPayload.daemon_started_at!
    }))
  }, [bootstrapPayload?.daemon_started_at, focusMatches, livePlanScenario, liveWorkScenarios, predictionScenarios, scenarioMap, scenarioResult.scenarios, scenarioScope])

  const preparedCards = useMemo(() => {
    const freshnessRank = (label: string) => {
      const lower = label.toLowerCase()
      if (lower.includes('fresh')) return 3
      if (lower.includes('recent')) return 2
      if (lower.includes('stale')) return 0
      return 1
    }
    const confidenceRank = (label: string) => {
      const lower = label.toLowerCase()
      if (lower.includes('high')) return 3
      if (lower.includes('medium')) return 2
      if (lower.includes('low')) return 0
      return 1
    }
    return [...preparedWork.cards].sort((a, b) => {
      const actionDelta = Number(Boolean(b.primary_action?.endpoint)) - Number(Boolean(a.primary_action?.endpoint))
      if (actionDelta) return actionDelta
      const confidenceDelta = confidenceRank(b.confidence_label) - confidenceRank(a.confidence_label)
      if (confidenceDelta) return confidenceDelta
      const freshnessDelta = freshnessRank(b.freshness_label) - freshnessRank(a.freshness_label)
      if (freshnessDelta) return freshnessDelta
      const evidenceDelta = b.evidence_count - a.evidence_count
      if (evidenceDelta) return evidenceDelta
      return b.updated_at - a.updated_at
    })
  }, [preparedWork.cards])

  const activeWorkspace = focusRuntime.route?.effective_route?.workspace
    ?? focusState?.workspaces?.find((workspace) => workspace.id === focusState.active_workspace_id)
    ?? null
  const activeWorkspaceLabel = activeWorkspace?.display_name
    ?? (activeWorkspace?.canonical_path ? compactWorkspacePath(activeWorkspace.canonical_path) : activeWorkspace?.id)
    ?? null
  const activeWorkspacePath = activeWorkspace?.canonical_path ?? null

  useEffect(() => {
    if (!bootstrapPayload?.cockpit_sha || !COCKPIT_BUILD_SHA) {
      return
    }
    setBundleMismatch(bootstrapPayload.cockpit_sha !== COCKPIT_BUILD_SHA)
  }, [bootstrapPayload?.cockpit_sha])

  useEffect(() => {
    if (bootstrap.error) {
      setToast({ msg: `Bootstrap failed: ${bootstrap.error}`.slice(0, 100), color: 'var(--err)' })
    }
  }, [bootstrap.error])

  const handleProxyDecision = useCallback((record: DecisionRecordPayload) => {
    setDecisions((previous) => [record, ...previous.filter((item) => item.id !== record.id)].slice(0, 100))
  }, [])

  const proxyStream = useEvents<DecisionRecordPayload>({
    path: '/decisions/stream',
    enabled: mode === 'proxy',
    parse: (raw) => JSON.parse(raw) as DecisionRecordPayload,
    toEvent: adaptDecisionEvent,
    onPayload: handleProxyDecision,
  })

  useEffect(() => {
    document.documentElement.style.setProperty('--accent', ACCENT_MAP[cockpit.accent])
  }, [cockpit.accent])

  useEffect(() => {
    const pulseTarget = activeScenarioCandidateFromPipelineEvent(pipeline.events[0])
    if (!pulseTarget) {
      return
    }
    setActivePulses((previous) => new Set(previous).add(pulseTarget))
    const timeout = window.setTimeout(() => {
      setActivePulses((previous) => {
        const next = new Set(previous)
        next.delete(pulseTarget)
        return next
      })
    }, 1400)
    return () => window.clearTimeout(timeout)
  }, [pipeline.events])

  useEffect(() => {
    if (!scenarios.length) {
      return
    }
    if (!selectedId || !scenarios.some((scenario) => scenario.id === selectedId)) {
      selectScenario(scenarios[0].id)
    }
  }, [scenarios, selectedId])

  const scenarioAutoFocusTargetId = useMemo(
    () => activeScenarioIdFromLiveWork(pipeline.events, scenarios),
    [pipeline.events, scenarios],
  )
  const scenarioAutoFocusTarget = scenarios.find((scenario) => scenario.id === scenarioAutoFocusTargetId) ?? null

  useEffect(() => {
    if (!scenarioAutoFocus || view !== 'scenario-map' || !scenarioAutoFocusTargetId || selectedId === scenarioAutoFocusTargetId) {
      return
    }
    selectScenario(scenarioAutoFocusTargetId)
  }, [scenarioAutoFocus, scenarioAutoFocusTargetId, selectedId, view])

  useEffect(() => {
    if (!preparedCards.length) {
      return
    }
    if (!selectedWorkId || !preparedCards.some((card) => card.id === selectedWorkId)) {
      setSelectedWorkId(preparedCards[0].id)
    }
  }, [preparedCards, selectedWorkId])

  useEffect(() => {
    if (!decisions.length || selectedDecisionId) {
      return
    }
    setSelectedDecisionId(decisions[0].id)
  }, [decisions, selectedDecisionId])

  const refreshStatus = useCallback(async () => {
    const payload = await getStatus()
    if (payload.backend) {
      setBackend((current) => ({ ...(current ?? ({} as BackendSettings)), ...payload.backend } as BackendSettings))
    }
    if (payload.compute) {
      setCompute((current) => ({ ...(current ?? ({} as ComputeSettings)), ...payload.compute } as ComputeSettings))
    }
    if (payload.mcp) {
      setMcp((current) => ({ ...(current ?? ({} as MCPSettings)), ...payload.mcp } as MCPSettings))
    }
    if (payload.limits) {
      setLimits((current) => ({ ...(current ?? ({} as LimitSettings)), ...payload.limits } as LimitSettings))
    }
    if (typeof payload.gateway_enabled === 'boolean') {
      setGatewayEnabled(payload.gateway_enabled)
    }
    if (payload.prediction_metrics) {
      setPredictionMetrics(payload.prediction_metrics)
    }
    if (payload.prediction_calibration) {
      setPredictionCalibration(payload.prediction_calibration)
    }
    setFocusState(payload.focus ?? null)
  }, [])

  const refreshDevices = useCallback(async () => {
    try {
      const payload = await getComputeDevices()
      setDevices(payload.devices ?? [])
      setDevicesWarning(payload.warning ?? null)
    } catch {
      setDevicesWarning('Could not enumerate compute devices.')
    }
  }, [])

  const refreshPresets = useCallback(async () => {
    try {
      const payload = await getBackendPresets()
      setPresets(payload.presets ?? [])
    } catch (error) {
      showToast(String(error instanceof Error ? error.message : 'Could not load backend presets').slice(0, 80), 'var(--err)')
    }
  }, [])

  const refreshSkillsPinned = useCallback(async () => {
    const [skillsPayload, pinnedPayload] = await Promise.all([listSkills(), listPinnedFacts()])
    setSkills(skillsPayload.skills)
    setPinnedFacts(pinnedPayload.facts)
  }, [])

  const refreshProxyData = useCallback(async () => {
    const [impactPayload, decisionsPayload] = await Promise.all([getImpactSummary(), listDecisions()])
    setImpact(impactPayload)
    setDecisions(decisionsPayload.items)
  }, [])

  const refreshPredictions = useCallback(async () => {
    const payload = await listPredictionsByState()
    setPredictions(flattenPredictions(payload))
  }, [])

  const refreshSourceState = useCallback(async () => {
    const [sourcesPayload, capabilitiesPayload] = await Promise.all([getSourcesPermissions(), getSignalCapabilities()])
    setSourcesPermissions(sourcesPayload)
    setSignalCapabilities(capabilitiesPayload)
  }, [])

  const refreshExternalState = useCallback(async () => {
    try {
      setExternalState(await getExternalState())
    } catch {
      setExternalState(null)
    }
  }, [])

  const refreshAll = useCallback(async () => {
    await Promise.all([refreshStatus(), refreshDevices(), refreshPresets(), refreshSkillsPinned(), preparedWork.refresh(), refreshPredictions(), focusRuntime.refresh(), refreshSourceState(), refreshExternalState(), activeWork.refresh()])
    if (mode === 'proxy') {
      await refreshProxyData()
    }
  }, [activeWork.refresh, focusRuntime.refresh, mode, preparedWork.refresh, refreshDevices, refreshExternalState, refreshPredictions, refreshPresets, refreshProxyData, refreshSkillsPinned, refreshSourceState, refreshStatus])

  useEffect(() => {
    refreshAll().catch(() => {
      setToast({ msg: 'Initial load failed', color: 'var(--err)' })
    })
  }, [refreshAll])

  useEffect(() => {
    const interval = window.setInterval(() => {
      refreshStatus().catch(() => undefined)
      refreshPredictions().catch(() => undefined)
      refreshSourceState().catch(() => undefined)
      refreshExternalState().catch(() => undefined)
      activeWork.refresh().catch(() => undefined)
      if (mode === 'proxy') {
        getImpactSummary().then(setImpact).catch(() => undefined)
      }
    }, 15000)
    return () => window.clearInterval(interval)
  }, [activeWork.refresh, mode, refreshExternalState, refreshPredictions, refreshSourceState, refreshStatus])

  function showToast(msg: string, color: string) {
    setToast({ msg, color })
    window.setTimeout(() => setToast(null), 2200)
  }

  function selectScenario(id: string | null) {
    setSelectedId(id)
    if (id && isPredictionScenarioId(id)) {
      setLiveSelection({ entityType: 'prediction', entityId: id.replace(/^prediction:/, '') })
    } else if (id) {
      const scenario = scenarios.find((item) => item.id === id)
      if (scenario?.parent && isPredictionScenarioId(scenario.parent)) {
        setLiveSelection({ entityType: 'prediction', entityId: scenario.parent.replace(/^prediction:/, '') })
        return
      }
      setLiveSelection(null)
    } else {
      setLiveSelection(null)
    }
  }

  function selectPrediction(predictionId: string) {
    setLiveSelection({ entityType: 'prediction', entityId: predictionId.replace(/^prediction:/, '') })
  }

  async function applyScenarioPayload(payload: ScenarioApiPayload | null) {
    if (!payload) {
      return
    }

    setScenarioMap((current) => ({ ...current, [payload.id]: payload }))
    const adapted = adaptScenario(payload)
    setScenarios((current) => {
      const existing = current.find((item) => item.id === adapted.id)
      if (existing) {
        return current.map((item) => (item.id === adapted.id ? adapted : item))
      }
      return [adapted, ...current]
    })
  }

  async function handleFeedback(id: string, result: 'useful' | 'partial' | 'irrelevant') {
    if (isPredictionScenarioId(id)) {
      showToast('Prediction feedback is available from Prepared Work.', 'var(--fg-4)')
      return
    }
    try {
      const response = await sendOutcome(id, result)
      await applyScenarioPayload(response.scenario)
      showToast(`Recorded ${result}`, result === 'useful' ? 'var(--ok)' : result === 'partial' ? 'var(--amber)' : 'var(--fg-4)')
    } catch {
      showToast(`Failed to record ${result}`, 'var(--err)')
    }
  }

  async function handleTogglePin(id: string) {
    if (isPredictionScenarioId(id)) {
      showToast('Live preparation nodes cannot be pinned yet.', 'var(--fg-4)')
      return
    }
    const scenario = scenarios.find((item) => item.id === id)
    if (!scenario) {
      return
    }
    try {
      const response = await toggleScenarioPin(id, !scenario.pinned)
      await applyScenarioPayload(response.scenario)
      showToast(scenario.pinned ? 'Scenario unpinned' : 'Scenario pinned', 'var(--amber)')
    } catch {
      showToast('Failed to update pin state', 'var(--err)')
    }
  }

  async function handleExpand(id: string) {
    if (isPredictionScenarioId(id)) {
      showToast('Live preparation expands in the background.', 'var(--fg-4)')
      return
    }
    try {
      const response = await expandScenario(id)
      await applyScenarioPayload(response.scenario)
      showToast('Scenario expanded', 'var(--accent)')
    } catch {
      showToast('Failed to expand scenario', 'var(--err)')
    }
  }

  async function handleUnpinFact(id: string) {
    try {
      await deletePinnedFact(id)
      const payload = await listPinnedFacts()
      setPinnedFacts(payload.facts)
      showToast('Context unpinned', 'var(--amber)')
    } catch {
      showToast('Failed to unpin context', 'var(--err)')
    }
  }

  async function saveBackend(patch: Partial<BackendSettings>) {
    try {
      const response = await updateBackend(patch)
      setBackend(response.backend)
      showToast('Backend saved', 'var(--ok)')
    } catch (error) {
      showToast(String(error instanceof Error ? error.message : 'Failed to save backend').slice(0, 80), 'var(--err)')
    }
  }

  async function saveCompute(patch: Partial<ComputeSettings>) {
    try {
      const response = await updateCompute(patch)
      setCompute(response.compute)
      await refreshDevices()
      showToast('Compute saved', 'var(--ok)')
    } catch (error) {
      showToast(String(error instanceof Error ? error.message : 'Failed to save compute').slice(0, 80), 'var(--err)')
    }
  }

  async function saveMcp(patch: Partial<MCPSettings>) {
    try {
      const response = await updateMcp(patch)
      setMcp(response.mcp)
      showToast('MCP saved', 'var(--ok)')
    } catch (error) {
      showToast(String(error instanceof Error ? error.message : 'Failed to save MCP').slice(0, 80), 'var(--err)')
    }
  }

  async function saveExternalState(patch: Partial<ExternalStateSettings>) {
    try {
      const response = await updateExternalState({
        enabled: patch.enabled,
        max_calls_per_cycle: patch.max_calls_per_cycle,
        max_cycle_ms: patch.max_cycle_ms,
      })
      setExternalState(response)
      showToast('External data saved', 'var(--ok)')
    } catch (error) {
      showToast(String(error instanceof Error ? error.message : 'Failed to save external data').slice(0, 80), 'var(--err)')
    }
  }

  async function saveExternalFinance(patch: Partial<ExternalStateSettings['finance']>) {
    try {
      const response = await updateExternalFinance(patch)
      setExternalState(response)
      showToast('Finance data saved', 'var(--ok)')
    } catch (error) {
      showToast(String(error instanceof Error ? error.message : 'Failed to save finance data').slice(0, 80), 'var(--err)')
    }
  }

  async function handleSaveExternalProvider(payload: Parameters<typeof saveExternalProvider>[0]) {
    try {
      const response = await saveExternalProvider(payload)
      setExternalState(response)
      showToast('Provider saved', 'var(--ok)')
    } catch (error) {
      showToast(String(error instanceof Error ? error.message : 'Failed to save provider').slice(0, 80), 'var(--err)')
    }
  }

  async function handleDiscoverExternalProvider(providerId: string, apply = false) {
    try {
      const discovery = await discoverExternalProvider(providerId, { apply })
      setExternalStateDiscovery(discovery)
      if (apply) {
        await refreshExternalState()
      }
      showToast(apply ? 'Safe tools applied' : 'Provider discovered', 'var(--ok)')
    } catch (error) {
      showToast(String(error instanceof Error ? error.message : 'Discovery failed').slice(0, 80), 'var(--err)')
    }
  }

  async function saveContextTokens(maxContextTokens: number) {
    try {
      const response = await updateContext(maxContextTokens)
      setLimits((current) => ({ ...(current ?? ({} as LimitSettings)), ...response.limits }))
      showToast('Context limit saved', 'var(--ok)')
    } catch (error) {
      showToast(String(error instanceof Error ? error.message : 'Failed to save context').slice(0, 80), 'var(--err)')
    }
  }

  async function handleToggleGateway(enabled: boolean) {
    try {
      await toggleGateway(enabled)
      setGatewayEnabled(enabled)
    } catch {
      showToast('Failed to toggle gateway', 'var(--err)')
    }
  }

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) {
        return
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setPaletteOpen(true)
        return
      }
      if ((event.metaKey || event.ctrlKey) && event.key === ',') {
        event.preventDefault()
        setDrawerOpen(true)
        return
      }
      if (event.key === 'Escape') {
        setPaletteOpen(false)
        setDrawerOpen(false)
        return
      }
      if (mode !== 'proxy') {
        const shortcutView: Record<string, CockpitView> = {
          '1': 'focus',
          '2': 'prepared-work',
          '3': 'scenario-map',
          '4': 'timeline',
          '5': 'evidence',
          '6': 'now',
          '7': 'heatmap',
          '8': 'board',
        }
        const nextView = shortcutView[event.key]
        if (nextView) {
          setView(nextView)
          event.preventDefault()
          return
        }
      }

      if (mode !== 'proxy' && selectedId) {
        const index = scenarios.findIndex((scenario) => scenario.id === selectedId)
        if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
          const next = scenarios[(index + 1 + scenarios.length) % scenarios.length]
          if (next) {
            selectScenario(next.id)
          }
          event.preventDefault()
        }
        if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
          const next = scenarios[(index - 1 + scenarios.length) % scenarios.length]
          if (next) {
            selectScenario(next.id)
          }
          event.preventDefault()
        }
        if (event.key.toLowerCase() === 'u') {
          void handleFeedback(selectedId, 'useful')
        }
        if (event.key.toLowerCase() === 'p') {
          void handleFeedback(selectedId, 'partial')
        }
        if (event.key.toLowerCase() === 'x') {
          void handleFeedback(selectedId, 'irrelevant')
        }
        if (event.key === '.' || event.key === '•') {
          void handleTogglePin(selectedId)
        }
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
    // handleFeedback / handleTogglePin are stable handlers defined inline;
    // re-binding the listener on every render would churn for no benefit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, scenarios, selectedId])

  const selectedScenario = scenarios.find((scenario) => scenario.id === selectedId) ?? null

  useEffect(() => {
    if (!selectedScenario || selectedScenario.freshness === 'fresh') return
    if (isPredictionScenarioId(selectedScenario.id)) return
    if (invalidationById[selectedScenario.id] !== undefined) return
    let cancelled = false
    void fetchScenarioDetail(selectedScenario.id)
      .then((detail) => {
        if (cancelled) return
        setInvalidationById((prev) => ({
          ...prev,
          [selectedScenario.id]: detail.latest_invalidation_signal ?? null,
        }))
      })
      .catch(() => {
        if (!cancelled) {
          setInvalidationById((prev) => ({ ...prev, [selectedScenario.id]: null }))
        }
      })
    return () => {
      cancelled = true
    }
  }, [selectedScenario, invalidationById])
  const selectedDecision = decisions.find((decision) => decision.id === selectedDecisionId) ?? null

  const evidenceById = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(scenarioMap).map(([id, payload]) => [id, adaptEvidence(payload)]),
      ),
    [scenarioMap],
  )

  const scoreComponentsById = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(scenarioMap).map(([id, payload]) => [
          id,
          (payload.lifecycle_components ?? payload.score_components ?? []) as ScoreComponent[],
        ]),
      ),
    [scenarioMap],
  )

  const preparedById = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(scenarioMap)
          .filter(([, payload]) => payload.prepared_context)
          .map(([id, payload]) => [id, payload.prepared_context ?? '']),
      ),
    [scenarioMap],
  )

  const packageState = mode === 'proxy' ? proxyPackageFromDecision(selectedDecision) : null

  const commands = useMemo<CommandItem[]>(() => {
    const common: CommandItem[] = [
      { id: 'refresh', kind: 'action', label: 'Refresh cockpit data', hint: '↻', run: () => void refreshAll() },
      { id: 'settings', kind: 'action', label: 'Open settings', hint: '⌘,', run: () => setDrawerOpen(true) },
      { id: 'view-focus', kind: 'view', label: 'View Overview', hint: '1', run: () => setView('focus') },
      { id: 'view-prepared-work', kind: 'view', label: 'View Ready Work', hint: '2', run: () => setView('prepared-work') },
      { id: 'view-scenario-map', kind: 'view', label: 'View Map', hint: '3', run: () => setView('scenario-map') },
      { id: 'view-timeline', kind: 'view', label: 'View Activity', hint: '4', run: () => setView('timeline') },
      { id: 'view-evidence', kind: 'view', label: 'View Diagnostics', hint: '5', run: () => setView('evidence') },
      { id: 'view-now', kind: 'view', label: 'View Legacy Now', hint: '6', run: () => setView('now') },
      { id: 'view-heatmap', kind: 'view', label: 'View Heatmap Replay', hint: '7', run: () => setView('heatmap') },
      { id: 'view-board', kind: 'view', label: 'View Board', hint: '8', run: () => setView('board') },
      {
        id: 'clear-events',
        kind: 'action',
        label: 'Clear event stream',
        run: () => (mode === 'proxy' ? proxyStream.setEvents([]) : pipeline.reset()),
      },
    ]

    if (mode !== 'proxy' && selectedId) {
      common.push({
        id: 'expand-selected',
        kind: 'action',
        label: 'Expand selected scenario',
        run: () => void handleExpand(selectedId),
      })
    }

    if (mode !== 'proxy') {
      return [
        ...common,
        ...scenarios.map((scenario) => ({
          id: scenario.id,
          kind: scenario.kind,
          kindColor: KIND_COLOR[scenario.kind],
          label: scenario.title,
          keywords: `${scenario.path} ${scenario.id}`,
          hint: `${Math.round(scenario.relevance * 100)}% relevant`,
          run: () => selectScenario(scenario.id),
        })),
      ]
    }

    return [
      ...common,
      ...decisions.map((decision) => ({
        id: decision.id,
        kind: 'decision',
        kindColor: 'var(--accent)',
        label: decision.id,
        keywords: `${decision.prompt} ${decision.prompt_hash}`,
        hint: `${decision.token_used} tok`,
        run: () => setSelectedDecisionId(decision.id),
      })),
    ]
    // handleExpand is stable and only used inside a closure captured once
    // per command-palette render; including it would cause a render loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [decisions, mode, pipeline, proxyStream, refreshAll, scenarios, selectedId])

  const streamEvents = mode === 'proxy' ? proxyStream.events : pipeline.events
  const streamLive = mode === 'proxy' ? proxyStream.live : pipeline.live

  const showScenarioPane = mode !== 'proxy'
  const scenarioHeading =
    scenarioScope === 'live'
      ? 'live suggestions'
      : scenarioScope === 'focus'
        ? 'focus suggestions'
        : scenarioScope === 'history'
          ? 'archived suggestions'
        : 'session suggestions'
  const scenarioEmptyHint =
    scenarioScope === 'live'
      ? 'No suggestions or live preparation nodes are available yet.'
      : scenarioScope === 'focus'
        ? 'No suggestions or live preparation nodes for the current Auto Focus workspace yet.'
        : scenarioScope === 'history'
          ? 'No archived suggestions yet. Cooling scenarios will move here instead of staying on the live map.'
        : 'No suggestions have been created in this cockpit session yet. Switch to History to inspect older suggestions.'
  const embeddedInspector = view === 'heatmap' && mode !== 'proxy'
  const showRightRail =
    !embeddedInspector && (mode === 'proxy' || view === 'scenario-map' || view === 'timeline' || view === 'evidence')
  const wideGraph = embeddedInspector || !showRightRail
  const reviewablePreparedCount = preparedCards.filter(isReviewablePreparedCard).length

  return (
    <div className={wideGraph ? 'cockpit-root cockpit-root--wide-graph' : 'cockpit-root'}>
      <TopBar
        mode={mode}
        query={query}
        onQuery={setQuery}
        running={streamLive}
        onToggleRun={() => void refreshAll()}
        packageState={packageState}
        onOpenSettings={() => setDrawerOpen(true)}
        onOpenPalette={() => setPaletteOpen(true)}
        view={mode === 'proxy' ? undefined : view}
        onChangeView={mode === 'proxy' ? undefined : setView}
      />

      <LeftRail
        mode={mode}
        skills={skills}
        pinned={pinnedFacts}
        packageState={packageState}
        onUnpin={(id) => void handleUnpinFact(id)}
        scenarioCount={scenarios.length}
        impact={impact}
        quiet={mode !== 'proxy'}
        preparedCount={reviewablePreparedCount || preparedCards.length}
        workerState={focusRuntime.jobs?.worker?.state ?? activeWork.snapshot?.phase ?? null}
        live={streamLive}
        modelName={pipeline.model.lastModel ?? null}
        workspaceLabel={activeWorkspaceLabel}
        workspacePath={activeWorkspacePath}
        header={
          <SystemVitals
            live={streamLive}
            mode={mode}
            cycle={pipeline.cycle}
            model={pipeline.model}
            scenarioCount={scenarios.length}
            pendingLlm={pipeline.model.pending.size}
            predictionMetrics={predictionMetrics}
            predictionCalibration={predictionCalibration}
          />
        }
        footer={mode === 'proxy' ? null : <LearningPanel />}
      />

      {showScenarioPane ? (
        <div style={{ gridArea: 'graph', position: 'relative', background: 'var(--bg-0)', overflow: 'hidden', display: 'grid', gridTemplateRows: 'auto minmax(0, 1fr)' }}>
          {view === 'scenario-map' ? (
            <>
              <ScenarioMapControls
                scope={scenarioScope}
                onScope={setScenarioScope}
                focusMatches={focusMatches}
              />
              {scenarioResult.error ? (
                <div role="alert" style={graphNoticeStyle}>
                  Could not load scenarios: {scenarioResult.error}
                </div>
              ) : scenarioScope === 'focus' && !focusMatches ? (
                <div role="status" style={graphNoticeStyle}>
                  Auto Focus is working in another workspace.
                </div>
              ) : null}
              <PipelineCanvas
                scenarios={scenarios}
                activePlan={activeWork.snapshot?.plan_draft ?? null}
                heading={scenarioHeading}
                emptyHint={scenarioEmptyHint}
                selectedId={selectedId}
                onSelect={selectScenario}
                autoFocusEnabled={scenarioAutoFocus}
                autoFocusTargetId={scenarioAutoFocusTargetId}
                autoFocusTargetLabel={scenarioAutoFocusTarget?.title ?? null}
                onAutoFocusChange={setScenarioAutoFocus}
                activePulses={activePulses}
                pinnedIds={new Set(scenarios.filter((scenario) => scenario.pinned).map((scenario) => scenario.id))}
                signals={pipeline.signals}
                targets={pipeline.targets}
                artefacts={pipeline.artefacts}
                decisions={pipeline.decisions}
                model={pipeline.model}
                cycle={pipeline.cycle}
                events={pipeline.events}
              />
            </>
          ) : (
            <div style={{ minHeight: 0, overflow: 'hidden' }}>
              {view === 'focus' ? (
                <OperatorOverviewView
                  cards={preparedCards}
                  loading={preparedWork.loading}
                  error={preparedWork.error}
                  selectedId={selectedWorkId}
                  onSelect={setSelectedWorkId}
                  onCardsChange={preparedWork.setCards}
                  onAction={(message) => showToast(message, message.startsWith('HTTP') || message.startsWith('Failed') ? 'var(--err)' : 'var(--accent)')}
                  activeWork={activeWork.snapshot}
                  jobs={focusRuntime.jobs}
                  route={focusRuntime.route}
                  activity={focusRuntime.activity}
                  predictions={predictions}
                  externalState={externalState}
                  onSelectPrediction={selectPrediction}
                />
              ) : null}
              {view === 'prepared-work' ? (
                <PreparedWorkView
                  cards={preparedCards}
                  loading={preparedWork.loading}
                  error={preparedWork.error}
                  route={focusRuntime.route}
                  selectedId={selectedWorkId}
                  onSelect={setSelectedWorkId}
                  onCardsChange={preparedWork.setCards}
                  onAction={(message) => showToast(message, message.startsWith('HTTP') || message.startsWith('Failed') ? 'var(--err)' : 'var(--accent)')}
                />
              ) : null}
              {view === 'now' ? (
                <NowView
                  cards={preparedCards}
                  predictions={predictions}
                  scenarios={scenarios}
                  activeWork={activeWork.snapshot}
                  selectedWorkId={selectedWorkId}
                  selectedScenarioId={selectedId}
                  onSelectWork={setSelectedWorkId}
                  onSelectScenario={selectScenario}
                  onSelectPrediction={selectPrediction}
                />
              ) : null}
              {view === 'heatmap' ? (
                <HeatmapReplayView
                  scenarios={scenarios}
                  events={pipeline.events}
                  selectedScenarioId={selectedId}
                  onSelectScenario={selectScenario}
                  onScenarioFeedback={(id, result) => void handleFeedback(id, result)}
                  onPinScenario={(id) => void handleTogglePin(id)}
                  onOpenScenarioMap={() => setView('scenario-map')}
                  onOpenEvidence={() => setView('evidence')}
                />
              ) : null}
              {view === 'timeline' ? (
                <TimelineView
                  events={pipeline.events}
                  live={pipeline.live}
                  pendingLlm={pipeline.model.pending.size}
                  onSelect={selectScenario}
                />
              ) : null}
              {view === 'board' ? (
                <BoardView
                  cards={preparedCards}
                  predictions={predictions}
                  scenarios={scenarios}
                  selectedWorkId={selectedWorkId}
                  selectedScenarioId={selectedId}
                  onSelectWork={setSelectedWorkId}
                  onSelectScenario={selectScenario}
                  onSelectPrediction={selectPrediction}
                />
              ) : null}
              {view === 'evidence' ? (
                <EvidenceView
                  cards={preparedCards}
                  scenarios={scenarios}
                  evidenceById={evidenceById}
                  selectedWorkId={selectedWorkId}
                  selectedScenarioId={selectedId}
                  onSelectWork={setSelectedWorkId}
                  onSelectScenario={selectScenario}
                />
              ) : null}
            </div>
          )}
          {toast ? (
            <div
              style={{
                position: 'absolute',
                bottom: 20,
                left: '50%',
                transform: 'translateX(-50%)',
                padding: '9px 16px',
                background: 'var(--bg-1)',
                border: `1px solid ${toast.color}`,
                borderRadius: 'var(--r-2)',
                color: toast.color,
                fontFamily: 'var(--font-mono)',
                fontSize: 11,
                letterSpacing: 0.3,
                boxShadow: '0 8px 30px rgba(0,0,0,.4)',
                animation: 'dc-fadein .2s',
              }}
            >
              {toast.msg}
            </div>
          ) : null}
        </div>
      ) : null}

      {!showScenarioPane ? (
        <div style={{ gridArea: 'graph', display: 'flex', flexDirection: 'column', background: 'var(--bg-1)', borderRight: '1px solid var(--line-1)', minWidth: 0 }}>
          <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--line-hair)' }}>
            <div className="mono" style={{ fontSize: 10, letterSpacing: 1.2, color: 'var(--fg-4)' }}>
              DECISIONS TIMELINE
            </div>
            <div style={{ fontSize: 15, color: 'var(--fg-1)', fontFamily: 'var(--font-display)', marginTop: 2 }}>
              {decisions.length} recent proxy decisions
            </div>
          </div>
          <div className="scroll" style={{ flex: 1, overflow: 'auto' }}>
            {decisions.map((decision) => (
              <button
                key={decision.id}
                onClick={() => setSelectedDecisionId(decision.id)}
                style={{
                  width: '100%',
                  textAlign: 'left',
                  padding: '14px 20px',
                  background: selectedDecisionId === decision.id ? 'color-mix(in oklch, var(--accent) 9%, var(--bg-1))' : 'transparent',
                  border: 'none',
                  borderBottom: '1px solid var(--line-hair)',
                  color: 'var(--fg-1)',
                  cursor: 'pointer',
                }}
              >
                <div className="mono" style={{ fontSize: 10.5, color: 'var(--accent)', letterSpacing: 0.6 }}>
                  {decision.id}
                </div>
                <div style={{ fontSize: 13, marginTop: 4, color: 'var(--fg-1)', lineHeight: 1.45 }}>
                  {decision.prompt}
                </div>
                <div className="mono" style={{ fontSize: 10.5, marginTop: 6, color: 'var(--fg-4)' }}>
                  {decision.token_used}/{decision.token_budget} tok · {decision.selection_count ?? decision.selections.length} selections
                </div>
              </button>
            ))}
            {!decisions.length ? (
              <div style={{ padding: 20, color: 'var(--fg-4)', fontSize: 12 }}>No decisions yet.</div>
            ) : null}
          </div>
        </div>
      ) : null}

      {showRightRail ? (
      <div
        style={{
          gridArea: 'stream',
          display: 'grid',
          gridTemplateRows: '1fr 1fr',
          gridTemplateColumns: '1fr',
          overflow: 'hidden',
          minHeight: 0,
        }}
      >
        <div style={{ minHeight: 0, overflow: 'hidden', borderBottom: '1px solid var(--line-1)', background: 'var(--bg-1)' }}>
          {showScenarioPane ? (
            liveSelection ? (
              <LiveWorkInspector
                snapshot={liveWork.snapshot}
                live={liveWork.live}
                error={liveWork.error}
                title={selectedScenario?.title}
                onClose={() => {
                  setLiveSelection(null)
                  if (selectedId && isPredictionScenarioId(selectedId)) {
                    setSelectedId(null)
                  }
                }}
              />
            ) : (
              <Inspector
                scenario={selectedScenario}
                scenarios={scenarios}
                evidenceById={evidenceById}
                scoreComponentsById={scoreComponentsById}
                preparedById={preparedById}
                invalidationById={invalidationById}
                onSelect={selectScenario}
                onFeedback={(id, result) => void handleFeedback(id, result)}
                onPin={(id) => void handleTogglePin(id)}
                pinnedIds={new Set(scenarios.filter((scenario) => scenario.pinned).map((scenario) => scenario.id))}
                onClose={() => selectScenario(null)}
              />
            )
          ) : (
            <div className="scroll" style={{ height: '100%', overflow: 'auto', padding: 18 }}>
              <div className="mono" style={{ fontSize: 10, letterSpacing: 1.2, color: 'var(--fg-4)', marginBottom: 10 }}>
                SELECTED DECISION
              </div>
              <pre
                style={{
                  background: 'var(--bg-inset)',
                  border: '1px solid var(--line-hair)',
                  borderRadius: 'var(--r-2)',
                  padding: 12,
                  color: 'var(--fg-2)',
                  fontSize: 11,
                  overflow: 'auto',
                }}
              >
                {selectedDecision ? JSON.stringify(selectedDecision, null, 2) : 'No decision selected.'}
              </pre>
            </div>
          )}
        </div>
        <div style={{ minHeight: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          <EventStreamPanel
            title={mode === 'proxy' ? 'DECISION STREAM' : 'EVENT STREAM'}
            subtitle={mode === 'proxy' ? 'Live proxy decisions' : 'Live daemon activity'}
            events={streamEvents}
            onSelect={mode === 'proxy' ? setSelectedDecisionId : selectScenario}
            live={streamLive}
            pendingLlm={mode === 'proxy' ? 0 : pipeline.model.pending.size}
          />
        </div>
      </div>
      ) : null}

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} commands={commands} />
      <SettingsDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        mode={mode}
        backend={backend}
        compute={compute}
        mcp={mcp}
        externalState={externalState}
        externalStateDiscovery={externalStateDiscovery}
        limits={limits}
        cockpit={cockpit}
        presets={presets}
        devices={devices}
        devicesWarning={devicesWarning}
        gatewayEnabled={gatewayEnabled}
        onSaveBackend={saveBackend}
        onSaveCompute={saveCompute}
        onSaveMcp={saveMcp}
        onSaveExternalState={saveExternalState}
        onSaveExternalFinance={saveExternalFinance}
        onSaveExternalProvider={handleSaveExternalProvider}
        onDiscoverExternalProvider={handleDiscoverExternalProvider}
        onSaveContext={saveContextTokens}
        onPatchCockpit={(patch) => setCockpit((current) => ({ ...current, ...patch }))}
        onToggleGateway={mode === 'proxy' ? handleToggleGateway : undefined}
      />
      {bundleMismatch ? <MismatchBanner onReload={() => window.location.reload()} /> : null}
    </div>
  )
}

function ScenarioMapControls({
  scope,
  onScope,
  focusMatches,
}: {
  scope: ScenarioScope
  onScope: (scope: ScenarioScope) => void
  focusMatches: boolean
}) {
  const scopeOptions: Array<{ id: ScenarioScope; label: string }> = [
    { id: 'live', label: 'Live' },
    { id: 'session', label: 'Session' },
    { id: 'focus', label: 'Focus' },
    { id: 'history', label: 'History' },
  ]
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 14,
        padding: '8px 14px',
        borderBottom: '1px solid var(--line-hair)',
        background: 'var(--bg-1)',
        minWidth: 0,
      }}
    >
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', minWidth: 0, overflow: 'hidden' }}>
        <span className="mono" style={{ fontSize: 9.5, letterSpacing: 1.1, color: 'var(--fg-4)', flexShrink: 0 }}>
          TYPES
        </span>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', minWidth: 0, overflowX: 'auto' }}>
          {Object.entries(KIND_COLOR).map(([kind, color]) => (
            <span key={kind} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, flexShrink: 0 }}>
              <span style={{ width: 8, height: 8, borderRadius: 2, background: color }} />
              <span className="mono" style={{ fontSize: 9.5, color: 'var(--fg-3)', letterSpacing: 0.5, textTransform: 'uppercase' }}>
                {kind}
              </span>
            </span>
          ))}
        </div>
      </div>
      <div role="tablist" aria-label="Scenario data scope" style={{ display: 'flex', border: '1px solid var(--line-1)', borderRadius: 'var(--r-1)', flexShrink: 0 }}>
        {scopeOptions.map((option) => (
          <button
            key={option.id}
            type="button"
            role="tab"
            aria-selected={scope === option.id}
            title={option.id === 'focus' && !focusMatches ? 'Auto Focus is currently reporting another workspace.' : undefined}
            onClick={() => onScope(option.id)}
            style={{
              background: scope === option.id ? 'var(--bg-2)' : 'transparent',
              border: 'none',
              borderLeft: option.id === 'live' ? 'none' : '1px solid var(--line-1)',
              color: scope === option.id ? 'var(--fg-1)' : 'var(--fg-3)',
              padding: '5px 9px',
              fontFamily: 'var(--font-mono)',
              fontSize: 10.5,
              cursor: 'pointer',
              whiteSpace: 'nowrap',
            }}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  )
}

const graphNoticeStyle: React.CSSProperties = {
  position: 'absolute',
  top: 54,
  left: '50%',
  transform: 'translateX(-50%)',
  zIndex: 4,
  padding: '8px 12px',
  border: '1px solid var(--amber)',
  borderRadius: 'var(--r-1)',
  background: 'var(--bg-1)',
  color: 'var(--fg-2)',
  fontSize: 12,
  boxShadow: '0 8px 24px rgba(0,0,0,.35)',
}

export default App
