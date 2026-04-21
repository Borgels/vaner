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
  listSkills,
  nudgeSkill as apiNudgeSkill,
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
import { useScenarios } from './api/useScenarios'
import type { ScoreComponent } from './components/Inspector'
import { FrontierGraph } from './components/FrontierGraph'
import { CommandPalette, LeftRail, MismatchBanner, SettingsDrawer, StreamPanel, TopBar, type CommandItem } from './components/chrome'
import { Inspector } from './components/Inspector'
import { ACCENT_MAP, DEFAULT_COCKPIT_SETTINGS, KIND_COLOR } from './lib/constants'
import type {
  BackendPreset,
  BackendSettings,
  CockpitSettings,
  ComputeDevice,
  ComputeSettings,
  DecisionRecordPayload,
  ImpactSummary,
  LimitSettings,
  MCPSettings,
  ScenarioApiPayload,
  UIEvent,
  UIPackageState,
  UIPinnedFact,
  UISkill,
} from './types'

const COCKPIT_BUILD_SHA = (import.meta as unknown as { env?: { VITE_COCKPIT_SHA?: string } }).env?.VITE_COCKPIT_SHA ?? ''

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
  const daemonEvents = useEvents({ path: '/events/stream' })
  const [cockpit, setCockpit] = useState<CockpitSettings>(DEFAULT_COCKPIT_SETTINGS)
  const { scenarios, setScenarios, scenarioMap, setScenarioMap } = useScenarios(cockpit.topK, daemonEvents.events)
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
  const [toast, setToast] = useState<{ msg: string; color: string } | null>(null)
  const [impact, setImpact] = useState<ImpactSummary>({ count: 0 })
  const [decisions, setDecisions] = useState<DecisionRecordPayload[]>([])
  const [activePulses, setActivePulses] = useState<Set<string>>(new Set())
  const [bundleMismatch, setBundleMismatch] = useState(false)

  const mode = bootstrap.mode

  useEffect(() => {
    if (!bootstrap.cockpit_sha || !COCKPIT_BUILD_SHA) {
      return
    }
    setBundleMismatch(bootstrap.cockpit_sha !== COCKPIT_BUILD_SHA)
  }, [bootstrap.cockpit_sha])

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
    const pulseTarget = daemonEvents.events[0]?.scn
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
  }, [daemonEvents.events])

  useEffect(() => {
    if (!scenarios.length || selectedId) {
      return
    }
    setSelectedId(scenarios[0].id)
  }, [scenarios, selectedId])

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
    } catch {
      setPresets([])
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

  const refreshAll = useCallback(async () => {
    await Promise.all([refreshStatus(), refreshDevices(), refreshPresets(), refreshSkillsPinned()])
    if (mode === 'proxy') {
      await refreshProxyData()
    }
  }, [mode, refreshDevices, refreshPresets, refreshProxyData, refreshSkillsPinned, refreshStatus])

  useEffect(() => {
    refreshAll().catch(() => {
      setToast({ msg: 'Initial load failed', color: 'var(--err)' })
    })
  }, [refreshAll])

  useEffect(() => {
    const interval = window.setInterval(() => {
      refreshStatus().catch(() => undefined)
      if (mode === 'proxy') {
        getImpactSummary().then(setImpact).catch(() => undefined)
      }
    }, 15000)
    return () => window.clearInterval(interval)
  }, [mode, refreshStatus])

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
    await deletePinnedFact(id)
    const payload = await listPinnedFacts()
    setPinnedFacts(payload.facts)
  }

  async function handleSkillNudge(name: string, delta: number) {
    try {
      const response = await apiNudgeSkill(name, delta)
      setSkills((current) =>
        current.map((skill) => (skill.name === response.name ? { ...skill, weight: response.weight } : skill)),
      )
    } catch {
      showToast(`Failed to nudge ${name}`, 'var(--err)')
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
      {
        id: 'clear-events',
        kind: 'action',
        label: 'Clear event stream',
        run: () => (mode === 'proxy' ? proxyStream.setEvents([]) : daemonEvents.setEvents([])),
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
  }, [daemonEvents, decisions, mode, proxyStream, refreshAll, scenarios, selectedId])

  const streamEvents = mode === 'proxy' ? proxyStream.events : daemonEvents.events
  const streamLive = mode === 'proxy' ? proxyStream.live : daemonEvents.live

  const showScenarioPane = mode !== 'proxy'

  return (
    <div className="cockpit-root">
      <TopBar
        mode={mode}
        running={streamLive}
        onToggleRun={() => void refreshAll()}
        packageState={packageState}
        onOpenSettings={() => setDrawerOpen(true)}
        onOpenPalette={() => setPaletteOpen(true)}
      />

      <LeftRail
        mode={mode}
        skills={skills}
        pinned={pinnedFacts}
        packageState={packageState}
        onUnpin={(id) => void handleUnpinFact(id)}
        onSkillNudge={(name, delta) => void handleSkillNudge(name, delta)}
        scenarioCount={scenarios.length}
        impact={impact}
      />

      {showScenarioPane ? (
        <div style={{ gridArea: 'graph', position: 'relative', background: 'var(--bg-0)', overflow: 'hidden' }}>
          <div style={{ position: 'absolute', top: 16, left: 20, zIndex: 3 }}>
            <div className="mono" style={{ fontSize: 10, letterSpacing: 1.2, color: 'var(--fg-4)' }}>
              FRONTIER · SCENARIO GRAPH
            </div>
            <div style={{ fontSize: 15, color: 'var(--fg-1)', fontFamily: 'var(--font-display)', marginTop: 2 }}>
              {scenarios.length} scenarios · drag nodes · scroll to zoom
            </div>
          </div>
          <div
            style={{
              position: 'absolute',
              top: 16,
              right: 16,
              display: 'flex',
              gap: 10,
              zIndex: 3,
              padding: '6px 10px',
              background: 'var(--bg-1)',
              border: '1px solid var(--line-1)',
              borderRadius: 'var(--r-2)',
            }}
          >
            {Object.entries(KIND_COLOR).map(([kind, color]) => (
              <span key={kind} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <span style={{ width: 8, height: 8, borderRadius: 2, background: color }} />
                <span className="mono" style={{ fontSize: 9.5, color: 'var(--fg-3)', letterSpacing: 0.5, textTransform: 'uppercase' }}>
                  {kind}
                </span>
              </span>
            ))}
          </div>
          <FrontierGraph
            scenarios={scenarios}
            selectedId={selectedId}
            onSelect={setSelectedId}
            activePulses={activePulses}
            pinnedIds={new Set(scenarios.filter((scenario) => scenario.pinned).map((scenario) => scenario.id))}
          />
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
      ) : (
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
      )}

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
          <StreamPanel
            title={mode === 'proxy' ? 'DECISION STREAM' : 'EVENT STREAM'}
            subtitle={mode === 'proxy' ? 'Live proxy decisions' : 'Live ponder loop'}
            events={streamEvents}
            onSelect={mode === 'proxy' ? setSelectedDecisionId : setSelectedId}
            live={streamLive}
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

export default App
