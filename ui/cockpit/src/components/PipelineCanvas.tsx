import { useEffect, useMemo, useRef, useState } from 'react'

import type {
  ArtefactRow,
  CycleState,
  DecisionRow,
  ModelState,
  PipelineEvent,
  SignalRow,
  TargetRow,
} from '../api/usePipelineEvents'
import type { PipelineStage, UIScenario } from '../types'
import { ScenarioCluster } from './ScenarioCluster'

interface LaneConfig {
  id: PipelineStage
  label: string
  sub: string
  accent: string
}

const LANES: LaneConfig[] = [
  { id: 'signals', label: 'SIGNALS', sub: 'ingest', accent: 'var(--fg-3)' },
  { id: 'targets', label: 'TARGETS', sub: 'planner', accent: 'var(--accent-soft)' },
  { id: 'model', label: 'MODEL', sub: 'llm', accent: 'var(--amber)' },
  { id: 'artefacts', label: 'ARTEFACTS', sub: 'store', accent: 'var(--kind-refactor)' },
  { id: 'scenarios', label: 'SCENARIOS', sub: 'frontier', accent: 'var(--accent)' },
  { id: 'decisions', label: 'DECISIONS', sub: 'proxy', accent: 'var(--kind-research)' },
]

export interface PipelineCanvasProps {
  scenarios: UIScenario[]
  selectedId: string | null
  onSelect: (id: string) => void
  activePulses: Set<string>
  pinnedIds: Set<string>
  signals: SignalRow[]
  targets: TargetRow[]
  artefacts: ArtefactRow[]
  decisions: DecisionRow[]
  model: ModelState
  cycle: CycleState
  events: PipelineEvent[]
}

/**
 * Top ribbon + scenario cluster that together form the cockpit's pipeline
 * view.
 *
 * The six-lane ribbon mirrors the daemon's real control flow:
 *   Signals → Targets → Model → Artefacts → Scenarios → Decisions
 *
 * Each lane owns a small live read-out of recent activity (latest path, count,
 * latency, etc.) and flashes when a fresh event for its stage arrives. The
 * scenario cluster underneath shows the actual graph of scenarios with
 * shared-path Jaccard edges — the concrete "connected structure" users can
 * see while the daemon works.
 */
export function PipelineCanvas({
  scenarios,
  selectedId,
  onSelect,
  activePulses,
  pinnedIds,
  signals,
  targets,
  artefacts,
  decisions,
  model,
  cycle,
  events,
}: PipelineCanvasProps) {
  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        display: 'grid',
        gridTemplateRows: 'auto minmax(0, 1fr)',
        background: 'var(--bg-0)',
      }}
    >
      <PipelineRibbon
        signals={signals}
        targets={targets}
        artefacts={artefacts}
        decisions={decisions}
        model={model}
        cycle={cycle}
        events={events}
        scenarios={scenarios}
      />

      <div style={{ position: 'relative', minHeight: 0 }}>
        <div style={{ position: 'absolute', top: 14, left: 20, zIndex: 3 }}>
          <div className="mono" style={{ fontSize: 10, letterSpacing: 1.2, color: 'var(--fg-4)' }}>
            SCENARIO CLUSTER
          </div>
          <div style={{ fontSize: 15, color: 'var(--fg-1)', fontFamily: 'var(--font-display)', marginTop: 2 }}>
            {scenarios.length} scenarios · shared-path edges · drag to reposition
          </div>
        </div>
        <ScenarioCluster
          scenarios={scenarios}
          selectedId={selectedId}
          onSelect={onSelect}
          activePulses={activePulses}
          pinnedIds={pinnedIds}
          emptyHint="Waiting for the daemon to produce scenarios. Trigger a cycle (Files change or run `vaner ponder once`) to see nodes appear here."
        />
      </div>
    </div>
  )
}

interface PipelineRibbonProps {
  signals: SignalRow[]
  targets: TargetRow[]
  artefacts: ArtefactRow[]
  decisions: DecisionRow[]
  model: ModelState
  cycle: CycleState
  events: PipelineEvent[]
  scenarios: UIScenario[]
}

function PipelineRibbon({
  signals,
  targets,
  artefacts,
  decisions,
  model,
  cycle,
  events,
  scenarios,
}: PipelineRibbonProps) {
  const ribbonRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 1000, h: 140 })

  useEffect(() => {
    const element = ribbonRef.current
    if (!element) {
      return
    }
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setSize({ w: entry.contentRect.width, h: entry.contentRect.height })
      }
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  const lastByStage = useMemo(() => {
    const map = new Map<PipelineStage, number>()
    for (const event of events) {
      if (!map.has(event.stage)) {
        map.set(event.stage, event.ts * 1000)
      }
    }
    return map
  }, [events])

  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 400)
    return () => window.clearInterval(timer)
  }, [])

  const pendingLlm = model.pending.size

  return (
    <div
      ref={ribbonRef}
      style={{
        position: 'relative',
        borderBottom: '1px solid var(--line-1)',
        background: 'linear-gradient(180deg, var(--bg-1), var(--bg-0))',
        padding: '14px 20px 12px',
        overflow: 'hidden',
      }}
      aria-label="Pipeline ribbon"
    >
      <ParticleTrack width={size.w - 40} events={events} />
      <div
        style={{
          position: 'relative',
          display: 'grid',
          gridTemplateColumns: `repeat(${LANES.length}, minmax(0, 1fr))`,
          gap: 12,
          zIndex: 1,
        }}
      >
        {LANES.map((lane) => {
          const last = lastByStage.get(lane.id)
          const recent = last ? now - last < 1500 : false
          const busy = lane.id === 'model' && pendingLlm > 0
          return (
            <Lane
              key={lane.id}
              lane={lane}
              active={recent || busy}
              content={laneContent(lane.id, {
                signals,
                targets,
                artefacts,
                decisions,
                model,
                cycle,
                scenarios,
                pendingLlm,
              })}
            />
          )
        })}
      </div>
    </div>
  )
}

interface LaneProps {
  lane: LaneConfig
  active: boolean
  content: React.ReactNode
}

function Lane({ lane, active, content }: LaneProps) {
  return (
    <div
      data-lane={lane.id}
      style={{
        position: 'relative',
        padding: '10px 12px',
        border: `1px solid ${active ? lane.accent : 'var(--line-1)'}`,
        borderRadius: 'var(--r-2)',
        background: active
          ? `color-mix(in oklch, ${lane.accent} 10%, var(--bg-1))`
          : 'var(--bg-1)',
        minHeight: 78,
        transition: 'background .3s, border-color .3s',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
        <span className="mono" style={{ fontSize: 9.5, letterSpacing: 1.2, color: lane.accent }}>
          {lane.label}
        </span>
        <span className="mono" style={{ fontSize: 9, color: 'var(--fg-4)' }}>
          {lane.sub}
        </span>
      </div>
      <div style={{ marginTop: 6, fontSize: 11, color: 'var(--fg-2)', lineHeight: 1.45 }}>{content}</div>
    </div>
  )
}

interface LaneContext {
  signals: SignalRow[]
  targets: TargetRow[]
  artefacts: ArtefactRow[]
  decisions: DecisionRow[]
  model: ModelState
  cycle: CycleState
  scenarios: UIScenario[]
  pendingLlm: number
}

function laneContent(stage: PipelineStage, ctx: LaneContext): React.ReactNode {
  switch (stage) {
    case 'signals': {
      const last = ctx.signals[0]
      if (!last) {
        return <FaintLine text="awaiting signals" />
      }
      return (
        <div className="mono" style={{ fontSize: 10.5 }}>
          <div>fs {last.fsScan} · git {last.gitChanged}</div>
          <div style={{ color: 'var(--fg-4)' }}>{ctx.signals.length} bursts</div>
        </div>
      )
    }
    case 'targets': {
      const last = ctx.targets[0]
      if (!last) {
        return <FaintLine text="no targets planned" />
      }
      return (
        <div className="mono" style={{ fontSize: 10.5 }}>
          <div>{last.count} planned</div>
          <Truncated text={last.paths[0] ?? ''} muted />
        </div>
      )
    }
    case 'model': {
      if (ctx.pendingLlm > 0) {
        return (
          <div className="mono" style={{ fontSize: 10.5, color: 'var(--amber)' }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <Spinner />
              <span>{ctx.pendingLlm} in flight</span>
            </span>
            <Truncated text={ctx.model.lastModel ?? 'model'} muted />
          </div>
        )
      }
      return (
        <div className="mono" style={{ fontSize: 10.5 }}>
          <div>{ctx.model.lastLatencyMs ? `${ctx.model.lastLatencyMs.toFixed(0)}ms last` : 'standby'}</div>
          <Truncated text={ctx.model.lastModel ?? '—'} muted />
        </div>
      )
    }
    case 'artefacts': {
      const last = ctx.artefacts[0]
      if (!last) {
        return <FaintLine text="no artefacts yet" />
      }
      return (
        <div className="mono" style={{ fontSize: 10.5 }}>
          <div>{ctx.cycle.artefactsWritten} written</div>
          <Truncated text={last.path} muted />
        </div>
      )
    }
    case 'scenarios': {
      return (
        <div className="mono" style={{ fontSize: 10.5 }}>
          <div>{ctx.scenarios.length} on frontier</div>
          <div style={{ color: 'var(--fg-4)' }}>
            {ctx.scenarios.filter((scenario) => scenario.pinned).length} pinned
          </div>
        </div>
      )
    }
    case 'decisions': {
      const last = ctx.decisions[0]
      if (!last) {
        return <FaintLine text="no proxy decisions" />
      }
      return (
        <div className="mono" style={{ fontSize: 10.5 }}>
          <div>{last.decisionId}</div>
          <div style={{ color: 'var(--fg-4)' }}>
            {last.selectionCount} sel · {last.cacheTier || '—'}
          </div>
        </div>
      )
    }
    default:
      return null
  }
}

function Spinner() {
  return (
    <span
      aria-hidden
      style={{
        width: 9,
        height: 9,
        borderRadius: '50%',
        border: '1.5px solid var(--amber)',
        borderTopColor: 'transparent',
        animation: 'dc-spin 0.8s linear infinite',
        display: 'inline-block',
      }}
    />
  )
}

function FaintLine({ text }: { text: string }) {
  return (
    <span className="mono" style={{ fontSize: 10.5, color: 'var(--fg-4)' }}>
      {text}
    </span>
  )
}

function Truncated({ text, muted }: { text: string; muted?: boolean }) {
  return (
    <div
      title={text}
      style={{
        color: muted ? 'var(--fg-4)' : 'var(--fg-2)',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
        maxWidth: '100%',
      }}
    >
      {text || '—'}
    </div>
  )
}

interface ParticleTrackProps {
  width: number
  events: PipelineEvent[]
}

interface TrackParticle {
  id: string
  fromIndex: number
  toIndex: number
  color: string
  createdAt: number
}

const KIND_TRANSITIONS: Record<string, [PipelineStage, PipelineStage]> = {
  'signal.ingest': ['signals', 'targets'],
  'target.planned': ['targets', 'model'],
  'llm.request': ['targets', 'model'],
  'llm.response': ['model', 'artefacts'],
  'artefact.upsert': ['artefacts', 'scenarios'],
  'decision.recorded': ['scenarios', 'decisions'],
}

/**
 * SVG layer that renders short-lived particles flowing left-to-right between
 * lanes as new events arrive on the bus. Each particle lasts ~1.2s so the
 * ribbon always feels "alive" during an active cycle without piling up.
 */
function ParticleTrack({ width, events }: ParticleTrackProps) {
  const particlesRef = useRef<TrackParticle[]>([])
  const lastSeenRef = useRef<string | null>(null)
  const [now, setNow] = useState(() =>
    typeof performance !== 'undefined' ? performance.now() : 0,
  )

  useEffect(() => {
    if (!events.length) {
      return
    }
    const next: TrackParticle[] = []
    for (const event of events.slice(0, 15)) {
      if (event.id === lastSeenRef.current) {
        break
      }
      const transition = KIND_TRANSITIONS[event.kind]
      if (!transition) {
        continue
      }
      const fromIndex = LANES.findIndex((lane) => lane.id === transition[0])
      const toIndex = LANES.findIndex((lane) => lane.id === transition[1])
      if (fromIndex < 0 || toIndex < 0) {
        continue
      }
      next.push({
        id: `${event.id}-particle`,
        fromIndex,
        toIndex,
        color: event.color,
        createdAt: performance.now(),
      })
    }
    lastSeenRef.current = events[0].id
    if (next.length) {
      particlesRef.current = [...particlesRef.current.slice(-40), ...next]
      setNow(performance.now())
    }
  }, [events])

  useEffect(() => {
    let frame = 0
    const tick = (time: number) => {
      const active = particlesRef.current.filter((particle) => time - particle.createdAt < 1500)
      particlesRef.current = active
      if (active.length) {
        setNow(time)
      }
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [])

  if (!width || !LANES.length) {
    return null
  }

  const laneWidth = width / LANES.length
  const trackY = 48

  return (
    <svg
      width={width}
      height={78}
      style={{
        position: 'absolute',
        top: 6,
        left: 20,
        pointerEvents: 'none',
        opacity: 0.8,
      }}
      aria-hidden
    >
      {LANES.slice(0, -1).map((_, index) => (
        <line
          key={index}
          x1={laneWidth * (index + 0.5)}
          y1={trackY}
          x2={laneWidth * (index + 1.5)}
          y2={trackY}
          stroke="var(--line-1)"
          strokeWidth={0.6}
          strokeDasharray="2 4"
        />
      ))}
      {particlesRef.current.map((particle) => {
        const age = Math.min(1, (now - particle.createdAt) / 1100)
        const x = laneWidth * (particle.fromIndex + 0.5 + (particle.toIndex - particle.fromIndex) * age)
        const fade = 1 - Math.abs(0.5 - age) * 1.8
        return (
          <circle
            key={particle.id}
            cx={x}
            cy={trackY}
            r={3}
            fill={particle.color}
            opacity={Math.max(0.15, fade)}
          />
        )
      })}
    </svg>
  )
}
