import { useEffect, useMemo, useRef, useState } from 'react'

import { openEventSource } from './client'
import type { PipelineStage, UIEvent } from '../types'

/** Shape emitted by `/events/stream` after the unified event bus refactor. */
export interface PipelineEventPayload {
  id: string
  ts: number
  stage: PipelineStage
  kind: string
  payload: Record<string, unknown>
  scn: string | null
  path: string | null
  cycle_id: string | null
  // Legacy envelope preserved for one release.
  t: string
  tag: string
  color: string
  msg: string
}

export interface PipelineEvent extends UIEvent {
  stage: PipelineStage
  kind: string
  ts: number
  path: string | null
  cycleId: string | null
  payload: Record<string, unknown>
}

export interface SignalRow {
  cycleId: string | null
  ts: number
  fsScan: number
  gitChanged: number
  msg: string
}

export interface TargetRow {
  cycleId: string | null
  ts: number
  count: number
  paths: string[]
}

export interface ArtefactRow {
  id: string
  ts: number
  kind: string
  path: string
  cycleId: string | null
}

export interface DecisionRow {
  id: string
  ts: number
  decisionId: string
  selectionCount: number
  cacheTier: string
}

export interface ModelState {
  /** Currently pending requests keyed by request id. */
  pending: Map<string, { path: string | null; model: string; startedAt: number }>
  /** Last completed response. */
  lastLatencyMs: number | null
  lastModel: string | null
  recentLatencies: number[]
  totalRequests: number
  totalErrors: number
}

export interface CycleState {
  current: { cycleId: string; startedAt: number } | null
  lastFinished: { cycleId: string; written: number; durationMs: number; ts: number } | null
  totalCycles: number
  artefactsWritten: number
}

const MAX_EVENTS = 200
const MAX_ROWS = 20
const LATENCY_WINDOW = 20

function adaptLegacyEvent(payload: PipelineEventPayload): PipelineEvent {
  return {
    id: payload.id,
    t: payload.t ?? new Date((payload.ts ?? Date.now() / 1000) * 1000).toLocaleTimeString(),
    tag: payload.tag ?? payload.kind?.split('.')[0] ?? payload.stage,
    color: payload.color ?? 'var(--fg-3)',
    msg: payload.msg ?? `${payload.stage}:${payload.kind}`,
    scn: payload.scn ?? null,
    stage: payload.stage,
    kind: payload.kind,
    ts: payload.ts,
    path: payload.path ?? null,
    cycleId: payload.cycle_id ?? null,
    payload: payload.payload ?? {},
  }
}

interface Particle {
  id: string
  from: PipelineStage
  to: PipelineStage
  createdAt: number
  color: string
}

const LANE_TRANSITIONS: Partial<Record<string, PipelineStage[]>> = {
  'signal.ingest': ['signals', 'targets'],
  'target.planned': ['targets', 'model'],
  'llm.request': ['targets', 'model'],
  'llm.response': ['model', 'artefacts'],
  'artefact.upsert': ['artefacts', 'scenarios'],
  'decision.recorded': ['scenarios', 'decisions'],
}

interface UsePipelineEventsOptions {
  path?: string
  stages?: PipelineStage[]
  enabled?: boolean
}

export interface UsePipelineEventsResult {
  events: PipelineEvent[]
  live: boolean
  signals: SignalRow[]
  targets: TargetRow[]
  artefacts: ArtefactRow[]
  decisions: DecisionRow[]
  model: ModelState
  cycle: CycleState
  particles: Particle[]
  reset: () => void
}

/**
 * Subscribe to the unified `/events/stream` and maintain derived per-lane
 * state for the pipeline canvas. A single upstream subscription feeds every
 * lane/panel so particles, vitals, and the event list stay in lock-step.
 */
export function usePipelineEvents({
  path = '/events/stream',
  stages,
  enabled = true,
}: UsePipelineEventsOptions = {}): UsePipelineEventsResult {
  const [events, setEvents] = useState<PipelineEvent[]>([])
  const [live, setLive] = useState(false)
  const [signals, setSignals] = useState<SignalRow[]>([])
  const [targets, setTargets] = useState<TargetRow[]>([])
  const [artefacts, setArtefacts] = useState<ArtefactRow[]>([])
  const [decisions, setDecisions] = useState<DecisionRow[]>([])
  const [model, setModel] = useState<ModelState>(() => ({
    pending: new Map(),
    lastLatencyMs: null,
    lastModel: null,
    recentLatencies: [],
    totalRequests: 0,
    totalErrors: 0,
  }))
  const [cycle, setCycle] = useState<CycleState>({
    current: null,
    lastFinished: null,
    totalCycles: 0,
    artefactsWritten: 0,
  })
  const [particles, setParticles] = useState<Particle[]>([])

  const retryRef = useRef<number | undefined>(undefined)

  useEffect(() => {
    if (!enabled) {
      setLive(false)
      return
    }

    const url = stages && stages.length ? `${path}?stages=${stages.join(',')}` : path
    let source: EventSource | null = null
    let closed = false
    let attempt = 0

    const connect = () => {
      if (closed || document.visibilityState === 'hidden') {
        return
      }

      source = openEventSource(url)
      source.onopen = () => {
        attempt = 0
        setLive(true)
      }
      source.onerror = () => {
        setLive(false)
        source?.close()
        const nextDelay = Math.min(5000, 500 * 2 ** attempt)
        attempt += 1
        retryRef.current = window.setTimeout(connect, nextDelay)
      }
      source.onmessage = (message) => {
        try {
          const raw = JSON.parse(message.data) as PipelineEventPayload
          if (!raw.stage && !raw.kind) {
            // Drop messages that don't look like the new envelope.
            return
          }
          const event = adaptLegacyEvent(raw)
          setEvents((prev) => [event, ...prev].slice(0, MAX_EVENTS))
          applyEvent(event)
        } catch {
          // Ignore malformed events.
        }
      }
    }

    const applyEvent = (event: PipelineEvent) => {
      const lanePair = LANE_TRANSITIONS[event.kind]
      if (lanePair) {
        const [from, to] = lanePair
        setParticles((prev) => [
          ...prev.slice(-60),
          {
            id: `${event.id}-${from}-${to}`,
            from,
            to,
            createdAt: performance.now(),
            color: event.color,
          },
        ])
      }

      switch (event.kind) {
        case 'cycle.start':
          setCycle((prev) => ({
            ...prev,
            current: { cycleId: event.cycleId ?? event.id, startedAt: event.ts },
          }))
          break
        case 'cycle.end':
          setCycle((prev) => ({
            current: null,
            lastFinished: {
              cycleId: event.cycleId ?? event.id,
              written: Number(event.payload.written ?? 0),
              durationMs: Number(event.payload.duration_ms ?? 0),
              ts: event.ts,
            },
            totalCycles: prev.totalCycles + 1,
            artefactsWritten: prev.artefactsWritten + Number(event.payload.written ?? 0),
          }))
          break
        case 'signal.ingest':
          setSignals((prev) =>
            [
              {
                cycleId: event.cycleId,
                ts: event.ts,
                fsScan: Number(event.payload.fs_scan ?? 0),
                gitChanged: Number(event.payload.git_changed ?? 0),
                msg: event.msg,
              },
              ...prev,
            ].slice(0, MAX_ROWS),
          )
          break
        case 'target.planned':
          setTargets((prev) =>
            [
              {
                cycleId: event.cycleId,
                ts: event.ts,
                count: Number(event.payload.count ?? 0),
                paths: (event.payload.paths as string[] | undefined) ?? [],
              },
              ...prev,
            ].slice(0, MAX_ROWS),
          )
          break
        case 'artefact.upsert':
          setArtefacts((prev) =>
            [
              {
                id: event.id,
                ts: event.ts,
                kind: String(event.payload.kind ?? 'artefact'),
                path: event.path ?? String(event.payload.key ?? ''),
                cycleId: event.cycleId,
              },
              ...prev,
            ].slice(0, MAX_ROWS),
          )
          break
        case 'decision.recorded':
          setDecisions((prev) =>
            [
              {
                id: event.id,
                ts: event.ts,
                decisionId: String(event.payload.decision_id ?? 'decision'),
                selectionCount: Number(event.payload.selection_count ?? 0),
                cacheTier: String(event.payload.cache_tier ?? ''),
              },
              ...prev,
            ].slice(0, MAX_ROWS),
          )
          break
        case 'llm.request':
          setModel((prev) => {
            const next = new Map(prev.pending)
            next.set(event.id, {
              path: event.path,
              model: String(event.payload.model ?? 'model'),
              startedAt: event.ts,
            })
            return {
              ...prev,
              pending: next,
              totalRequests: prev.totalRequests + 1,
              lastModel: String(event.payload.model ?? prev.lastModel ?? 'model'),
            }
          })
          break
        case 'llm.response': {
          const requestId = String(event.payload.request_id ?? '')
          const latencyMs = Number(event.payload.latency_ms ?? 0)
          const ok = event.payload.ok !== false
          setModel((prev) => {
            const next = new Map(prev.pending)
            if (requestId) {
              next.delete(requestId)
            }
            return {
              ...prev,
              pending: next,
              lastLatencyMs: latencyMs,
              lastModel: String(event.payload.model ?? prev.lastModel ?? 'model'),
              recentLatencies: [...prev.recentLatencies, latencyMs].slice(-LATENCY_WINDOW),
              totalErrors: prev.totalErrors + (ok ? 0 : 1),
            }
          })
          break
        }
        default:
          break
      }
    }

    const onVisibilityChange = () => {
      if (document.visibilityState === 'hidden') {
        setLive(false)
        source?.close()
        source = null
        if (retryRef.current) {
          window.clearTimeout(retryRef.current)
        }
        return
      }

      connect()
    }

    connect()
    document.addEventListener('visibilitychange', onVisibilityChange)

    return () => {
      closed = true
      setLive(false)
      document.removeEventListener('visibilitychange', onVisibilityChange)
      source?.close()
      if (retryRef.current) {
        window.clearTimeout(retryRef.current)
      }
    }
  }, [enabled, path, stages])

  // Garbage-collect stale particles (>2s old) so the canvas stays fluid.
  useEffect(() => {
    if (!particles.length) {
      return
    }
    const timer = window.setInterval(() => {
      const now = performance.now()
      setParticles((prev) => prev.filter((particle) => now - particle.createdAt < 2000))
    }, 500)
    return () => window.clearInterval(timer)
  }, [particles.length])

  return useMemo(
    () => ({
      events,
      live,
      signals,
      targets,
      artefacts,
      decisions,
      model,
      cycle,
      particles,
      reset: () => {
        setEvents([])
        setSignals([])
        setTargets([])
        setArtefacts([])
        setDecisions([])
        setParticles([])
      },
    }),
    [events, live, signals, targets, artefacts, decisions, model, cycle, particles],
  )
}

export { adaptLegacyEvent }
