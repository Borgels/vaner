import { useCallback, useEffect, useMemo, useState } from 'react'

import { adaptEvidence, adaptScenario } from './api/adapt'
import {
  deletePinnedFact,
  expandScenario,
  getBackendPresets,
  getComputeDevices,
  getImpactSummary,
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
  updateMcp,
} from './api/client'
import { useBootstrap } from './api/useBootstrap'
import { useEvents } from './api/useEvents'
import { usePipelineEvents } from './api/usePipelineEvents'
import { usePreparedWork } from './api/usePreparedWork'
import { useScenarios } from './api/useScenarios'
import {
  BoardView,
  EvidenceView,
  NowView,
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
import { Inspector } from './components/Inspector'
import { LearningPanel } from './components/LearningPanel'
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
  ImpactSummary,
  LatestInvalidationSignal,
  LimitSettings,
  MCPSettings,
  PredictionSummary,
  ScenarioApiPayload,
  StatusPayload,
  UIEvent,
  UIPackageState,
  UIPinnedFact,
  UISkill,
} from './types'
import { fetchScenarioDetail } from './api/client'

const COCKPIT_BUILD_SHA = (import.meta as unknown as { env?: { VITE_COCKPIT_SHA?: string } }).env?.VITE_COCKPIT_SHA ?? ''

type ScenarioScope = 'all' | 'session' | 'focus'

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
  const preparedWork = usePreparedWork(24)
  const [cockpit, setCockpit] = useState<CockpitSettings>(DEFAULT_COCKPIT_SETTINGS)
  const [query, setQuery] = useState('')
  const scenarioResult = useScenarios(cockpit.topK, pipeline.events)
  const { setScenarios, scenarioMap, setScenarioMap } = scenarioResult
  const [scenarioScope, setScenarioScope] = useState<ScenarioScope>('session')
  const [backend, setBackend] = useState<BackendSettings | null>(null)
  const [compute, setCompute] = useState<ComputeSettings | null>(null)
  const [mcp, setMcp] = useState<MCPSettings | null>(null)
  const [limits, setLimits] = useState<LimitSettings | null>(null)
  const [presets, setPresets] = useState<BackendPreset[]>([])
  const [devices, setDevices] = useState<ComputeDevice[]>([])
  const [devicesWarning, setDevicesWarning] = useState<string | null>(null)
  const [gatewayEnabled, setGatewayEnabled] = useState<boolean>(false)
  const [skills, setSkills] = useState<UISkill[]>([])
  const [pinnedFacts, setPinnedFacts] = useState<UIPinnedFact[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedDecisionId, setSelectedDecisionId] = useState<string | null>(null)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [view, setView] = useState<CockpitView>('prepared-work')
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

  const bootstrapPayload = bootstrap.payload
  const mode = bootstrapPayload?.mode ?? 'daemon'
  const focusMatches =
    !bootstrapPayload?.workspace_id ||
    !focusState?.active_workspace_id ||
    focusState.active_workspace_id === bootstrapPayload.workspace_id
  const scenarios = useMemo(() => {
    if (scenarioScope === 'focus' && !focusMatches) {
      return []
    }
    if (scenarioScope !== 'session' || !bootstrapPayload?.daemon_started_at) {
      return scenarioResult.scenarios
    }
    return scenarioResult.scenarios.filter((scenario) => {
      if (scenario.freshness === 'stale') {
        return false
      }
      const refreshedAt = scenarioMap[scenario.id]?.last_refreshed_at ?? scenarioMap[scenario.id]?.created_at
      return typeof refreshedAt === 'number' && refreshedAt >= bootstrapPayload.daemon_started_at!
    })
  }, [bootstrapPayload?.daemon_started_at, focusMatches, scenarioMap, scenarioResult.scenarios, scenarioScope])

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
    const pulseTarget = pipeline.events[0]?.scn
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
      setSelectedId(scenarios[0].id)
    }
  }, [scenarios, selectedId])

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
    setPredictions(payload.predictions ?? [])
  }, [])

  const refreshAll = useCallback(async () => {
    await Promise.all([refreshStatus(), refreshDevices(), refreshPresets(), refreshSkillsPinned(), preparedWork.refresh(), refreshPredictions()])
    if (mode === 'proxy') {
      await refreshProxyData()
    }
  }, [mode, preparedWork.refresh, refreshDevices, refreshPredictions, refreshPresets, refreshProxyData, refreshSkillsPinned, refreshStatus])

  useEffect(() => {
    refreshAll().catch(() => {
      setToast({ msg: 'Initial load failed', color: 'var(--err)' })
    })
  }, [refreshAll])

  useEffect(() => {
    const interval = window.setInterval(() => {
      refreshStatus().catch(() => undefined)
      refreshPredictions().catch(() => undefined)
      if (mode === 'proxy') {
        getImpactSummary().then(setImpact).catch(() => undefined)
      }
    }, 15000)
    return () => window.clearInterval(interval)
  }, [mode, refreshPredictions, refreshStatus])

  function showToast(msg: string, color: string) {
    setToast({ msg, color })
    window.setTimeout(() => setToast(null), 2200)
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
    try {
      const response = await sendOutcome(id, result)
      await applyScenarioPayload(response.scenario)
      showToast(`Recorded ${result}`, result === 'useful' ? 'var(--ok)' : result === 'partial' ? 'var(--amber)' : 'var(--fg-4)')
    } catch {
      showToast(`Failed to record ${result}`, 'var(--err)')
    }
  }

  async function handleTogglePin(id: string) {
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
          '1': 'prepared-work',
          '2': 'now',
          '3': 'scenario-map',
          '4': 'timeline',
          '5': 'board',
          '6': 'evidence',
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
            setSelectedId(next.id)
          }
          event.preventDefault()
        }
        if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
          const next = scenarios[(index - 1 + scenarios.length) % scenarios.length]
          if (next) {
            setSelectedId(next.id)
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
          (payload.score_components ?? []) as ScoreComponent[],
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
      { id: 'view-prepared-work', kind: 'view', label: 'View Prepared Work', hint: '1', run: () => setView('prepared-work') },
      { id: 'view-now', kind: 'view', label: 'View Now', hint: '2', run: () => setView('now') },
      { id: 'view-scenario-map', kind: 'view', label: 'View Scenario Map', hint: '3', run: () => setView('scenario-map') },
      { id: 'view-timeline', kind: 'view', label: 'View Timeline', hint: '4', run: () => setView('timeline') },
      { id: 'view-board', kind: 'view', label: 'View Board', hint: '5', run: () => setView('board') },
      { id: 'view-evidence', kind: 'view', label: 'View Evidence', hint: '6', run: () => setView('evidence') },
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
          hint: scenario.score.toFixed(3),
          run: () => setSelectedId(scenario.id),
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
    scenarioScope === 'all'
      ? 'stored suggestions'
      : scenarioScope === 'focus'
        ? 'focus suggestions'
        : 'session suggestions'
  const scenarioEmptyHint =
    scenarioScope === 'all'
      ? 'No stored suggestions are available yet.'
      : scenarioScope === 'focus'
        ? 'No suggestions for the current Auto Focus workspace yet.'
        : 'No suggestions have been created in this cockpit session yet. Switch to History to inspect older stored suggestions.'

  return (
    <div className="cockpit-root">
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
                heading={scenarioHeading}
                emptyHint={scenarioEmptyHint}
                selectedId={selectedId}
                onSelect={setSelectedId}
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
              {view === 'prepared-work' ? (
                <PreparedWorkView
                  cards={preparedCards}
                  loading={preparedWork.loading}
                  error={preparedWork.error}
                  selectedId={selectedWorkId}
                  onSelect={setSelectedWorkId}
                  onCardsChange={preparedWork.setCards}
                  onAction={(message) => showToast(message, message.startsWith('HTTP') || message.startsWith('Failed') ? 'var(--err)' : 'var(--accent)')}
                />
              ) : null}
              {view === 'now' ? (
                <NowView
                  cards={preparedCards}
                  scenarios={scenarios}
                  selectedWorkId={selectedWorkId}
                  selectedScenarioId={selectedId}
                  onSelectWork={setSelectedWorkId}
                  onSelectScenario={setSelectedId}
                />
              ) : null}
              {view === 'timeline' ? (
                <TimelineView
                  events={pipeline.events}
                  live={pipeline.live}
                  pendingLlm={pipeline.model.pending.size}
                  onSelect={setSelectedId}
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
                  onSelectScenario={setSelectedId}
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
                  onSelectScenario={setSelectedId}
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
            <Inspector
              scenario={selectedScenario}
              scenarios={scenarios}
              evidenceById={evidenceById}
              scoreComponentsById={scoreComponentsById}
              preparedById={preparedById}
              invalidationById={invalidationById}
              onSelect={setSelectedId}
              onFeedback={(id, result) => void handleFeedback(id, result)}
              onPin={(id) => void handleTogglePin(id)}
              pinnedIds={new Set(scenarios.filter((scenario) => scenario.pinned).map((scenario) => scenario.id))}
              onClose={() => setSelectedId(null)}
            />
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
            onSelect={mode === 'proxy' ? setSelectedDecisionId : setSelectedId}
            live={streamLive}
            pendingLlm={mode === 'proxy' ? 0 : pipeline.model.pending.size}
          />
        </div>
      </div>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} commands={commands} />
      <SettingsDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        mode={mode}
        backend={backend}
        compute={compute}
        mcp={mcp}
        limits={limits}
        cockpit={cockpit}
        presets={presets}
        devices={devices}
        devicesWarning={devicesWarning}
        gatewayEnabled={gatewayEnabled}
        onSaveBackend={saveBackend}
        onSaveCompute={saveCompute}
        onSaveMcp={saveMcp}
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
    { id: 'session', label: 'Current session' },
    { id: 'focus', label: 'Current focus' },
    { id: 'all', label: 'History' },
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
              borderLeft: option.id === 'all' ? 'none' : '1px solid var(--line-1)',
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
