import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { KIND_COLOR } from '../lib/constants'
import type { UIScenario, UIScenarioKind } from '../types'

export type ClusterPosition = { x: number; y: number; vx?: number; vy?: number; _manual?: boolean }
const EXIT_RETENTION_MS = 2600
const LEGEND_KINDS: UIScenarioKind[] = ['research', 'explain', 'change', 'debug', 'refactor']

export interface ScenarioEdge {
  from: string
  to: string
  weight: number
  kind: 'parent' | 'shared-path'
}

export function scenarioScoreScale(score: number): number {
  if (score < 0.65) return 0.85
  if (score < 0.85) return 1
  if (score < 0.95) return 1.18
  return 1.35
}

export function scenarioReadinessScale(scenario: UIScenario): number {
  const readinessBase = {
    ready: 1.28,
    warming: 1.05,
    cooling: 0.88,
    unprepared: 0.78,
  }[scenario.readiness] ?? 0.9
  return Math.max(0.72, Math.min(1.38, readinessBase + (scenario.visiblePriority - 0.65) * 0.22))
}

export function scenarioTargetY(scenario: UIScenario, height: number): number {
  const top = 82
  const bottom = Math.max(top + 80, height - 72)
  const relevance = Math.max(0, Math.min(1, scenario.relevance))
  if (scenario.visibility === 'archived') {
    return bottom
  }
  return top + (1 - relevance) * (bottom - top)
}

export function scenarioFreshnessOpacity(scenario: UIScenario): number {
  if (scenario.decisionState === 'rejected') return 0.25
  if (scenario.lifecycleMotion === 'fading') return 0.28
  if (scenario.freshness === 'stale') return 0.48
  if (scenario.freshness === 'recent') return 0.72
  return 1
}

function isPlanScenario(scenario: UIScenario): boolean {
  return scenario.id.startsWith('plan_') || scenario.path.includes('/prep/plan_') || scenario.path.includes('plan draft')
}

function isPredictionScenario(scenario: UIScenario): boolean {
  return scenario.id.startsWith('prediction:')
}

function scenarioNodeBadge(scenario: UIScenario): string {
  if (isPlanScenario(scenario)) return 'plan'
  if (isPredictionScenario(scenario)) return 'prep'
  if (scenario.visibility === 'archived') return 'history'
  if (scenario.readiness === 'ready') return 'ready'
  return `${Math.round(scenario.relevance * 100)}%`
}

export function fadingScenario(scenario: UIScenario, nowMs = Date.now()): UIScenario {
  return {
    ...scenario,
    relevance: Math.min(scenario.relevance, 0.28),
    visiblePriority: Math.min(scenario.visiblePriority, 0.28),
    freshness: 'stale',
    readiness: 'cooling',
    visibility: 'archived',
    lifecycleMotion: 'fading',
    archivedAt: nowMs / 1000,
    visibilityReason: scenario.visibilityReason || 'no longer in the active daemon frontier',
  }
}

function scenarioMotionMarkerStyle(color: string, motion: UIScenario['lifecycleMotion']): React.CSSProperties | null {
  if (motion === 'stable') {
    return null
  }
  const pointsUp = motion === 'rising'
  return {
    position: 'absolute',
    right: -13,
    top: '50%',
    transform: 'translateY(-50%)',
    width: 0,
    height: 0,
    borderLeft: '4px solid transparent',
    borderRight: '4px solid transparent',
    borderBottom: pointsUp ? `7px solid ${color}` : undefined,
    borderTop: pointsUp ? undefined : `7px solid ${color}`,
    opacity: motion === 'fading' ? 0.45 : 0.8,
    filter: 'drop-shadow(0 0 4px rgba(0,0,0,.28))',
  }
}

/**
 * Jaccard similarity between two sets of path strings. Used to derive
 * implicit edges between scenarios that touch the same files even when they
 * don't share an explicit parent/child relationship.
 */
export function jaccard(a: Set<string>, b: Set<string>): number {
  if (!a.size || !b.size) {
    return 0
  }
  let intersection = 0
  for (const item of a) {
    if (b.has(item)) {
      intersection += 1
    }
  }
  const union = a.size + b.size - intersection
  return union > 0 ? intersection / union : 0
}

/**
 * Extract the set of file paths associated with a scenario. We read from the
 * ``entities`` array first (which surfaces touched files), then fall back to
 * the scenario's primary path so that even scenarios without entities can
 * form implicit shared-path edges with their direct neighbours.
 */
export function scenarioPaths(scenario: UIScenario): Set<string> {
  const paths = new Set<string>()
  if (scenario.path) {
    paths.add(scenario.path)
  }
  for (const entity of scenario.entities ?? []) {
    if (typeof entity === 'string' && entity) {
      paths.add(entity)
    }
  }
  return paths
}

/**
 * Compute scenario edges.
 *
 * Two sources are combined:
 *  1. Explicit ``parent`` links (rendered with a solid curve).
 *  2. Shared-path Jaccard overlap above ``threshold`` — limited to the top
 *     ``maxPerNode`` partners per scenario to keep the cluster legible on
 *     large frontiers.
 */
export function computeEdges(
  scenarios: UIScenario[],
  options: { threshold?: number; maxPerNode?: number } = {},
): ScenarioEdge[] {
  const threshold = options.threshold ?? 0.2
  const maxPerNode = options.maxPerNode ?? 3
  const edges: ScenarioEdge[] = []
  const seen = new Set<string>()

  for (const scenario of scenarios) {
    if (scenario.parent && scenarios.some((other) => other.id === scenario.parent)) {
      const key = `${scenario.parent}|${scenario.id}`
      if (!seen.has(key)) {
        seen.add(key)
        edges.push({ from: scenario.parent, to: scenario.id, weight: 1, kind: 'parent' })
      }
    }
  }

  const paths = scenarios.map((scenario) => ({ id: scenario.id, paths: scenarioPaths(scenario) }))
  for (let i = 0; i < paths.length; i += 1) {
    const candidates: Array<{ other: string; weight: number }> = []
    for (let j = 0; j < paths.length; j += 1) {
      if (i === j) {
        continue
      }
      const weight = jaccard(paths[i].paths, paths[j].paths)
      if (weight >= threshold) {
        candidates.push({ other: paths[j].id, weight })
      }
    }
    candidates.sort((a, b) => b.weight - a.weight)
    for (const candidate of candidates.slice(0, maxPerNode)) {
      const key = [paths[i].id, candidate.other].sort().join('|')
      if (seen.has(key)) {
        continue
      }
      seen.add(key)
      edges.push({
        from: paths[i].id,
        to: candidate.other,
        weight: candidate.weight,
        kind: 'shared-path',
      })
    }
  }

  return edges
}

/**
 * Kind-bucketed initial layout. Scenarios with the same ``kind`` get placed in
 * the same angular sector around the cluster centre; scenarios without a
 * parent still form a visible constellation rather than stacking in a single
 * column at x=0 the way the legacy tree layout did.
 */
export function initialLayout(
  scenarios: UIScenario[],
  width: number,
  height: number,
): Record<string, ClusterPosition> {
  const positions: Record<string, ClusterPosition> = {}
  const kinds = Array.from(new Set(scenarios.map((scenario) => scenario.kind))) as UIScenarioKind[]
  const centerX = width / 2
  const centerY = height / 2
  const radius = Math.min(width, height) * 0.35

  if (kinds.length === 0) {
    return positions
  }

  const byKind = new Map<UIScenarioKind, UIScenario[]>()
  for (const kind of kinds) {
    byKind.set(kind, [])
  }
  for (const scenario of scenarios) {
    byKind.get(scenario.kind)?.push(scenario)
  }

  kinds.forEach((kind, kindIndex) => {
    const bucket = byKind.get(kind) ?? []
    const sorted = [...bucket].sort((a, b) => b.relevance - a.relevance)
    const columnX = centerX + ((kindIndex - (kinds.length - 1) / 2) / Math.max(1, kinds.length)) * width * 0.72
    sorted.forEach((scenario, index) => {
      const localRadius = radius * 0.08 * (index % 3)
      const jitter = ((scenario.id.charCodeAt(0) % 7) - 3) * 0.04
      const spread = sorted.length > 1 ? ((index / (sorted.length - 1)) - 0.5) * 64 : 0
      const y = scenarioTargetY(scenario, height) + spread
      positions[scenario.id] = {
        x: Math.max(40, Math.min(width - 40, columnX + localRadius * Math.cos(jitter * Math.PI * 2))),
        y: Math.max(56, Math.min(height - 56, y + localRadius * Math.sin(jitter * Math.PI * 2))),
        vx: 0,
        vy: 0,
      }
    })
  })

  return positions
}

/**
 * Run one tick of a lightweight force-directed layout.
 *
 * Forces:
 *   - Coulomb-style repulsion between every pair (keeps nodes from stacking).
 *   - Hooke-style attraction along every edge (clusters share-path neighbours).
 *   - Weak centering gravity so orphan clusters don't drift off-screen.
 *
 * The output is mutated in place to avoid allocation churn in the animation
 * loop.
 */
export function tickForces(
  positions: Record<string, ClusterPosition>,
  edges: ScenarioEdge[],
  options: { width: number; height: number; dt?: number; gravityTargets?: Record<string, number> },
) {
  const dt = options.dt ?? 0.6
  const centerX = options.width / 2
  const centerY = options.height / 2
  const ids = Object.keys(positions)

  for (const id of ids) {
    const position = positions[id]
    if (!position._manual) {
      position.vx = (position.vx ?? 0) * 0.85
      position.vy = (position.vy ?? 0) * 0.85
    }
  }

  for (let i = 0; i < ids.length; i += 1) {
    const a = positions[ids[i]]
    for (let j = i + 1; j < ids.length; j += 1) {
      const b = positions[ids[j]]
      const dx = b.x - a.x
      const dy = b.y - a.y
      const distanceSquared = dx * dx + dy * dy + 0.01
      const distance = Math.sqrt(distanceSquared)
      const repulsion = 2400 / distanceSquared
      const fx = (dx / distance) * repulsion
      const fy = (dy / distance) * repulsion
      if (!a._manual) {
        a.vx = (a.vx ?? 0) - fx
        a.vy = (a.vy ?? 0) - fy
      }
      if (!b._manual) {
        b.vx = (b.vx ?? 0) + fx
        b.vy = (b.vy ?? 0) + fy
      }
    }
  }

  for (const edge of edges) {
    const a = positions[edge.from]
    const b = positions[edge.to]
    if (!a || !b) {
      continue
    }
    const dx = b.x - a.x
    const dy = b.y - a.y
    const distance = Math.sqrt(dx * dx + dy * dy) || 1
    const rest = edge.kind === 'parent' ? 130 : 160
    const stiffness = edge.kind === 'parent' ? 0.04 : 0.02 * edge.weight
    const delta = distance - rest
    const fx = (dx / distance) * delta * stiffness
    const fy = (dy / distance) * delta * stiffness
    if (!a._manual) {
      a.vx = (a.vx ?? 0) + fx
      a.vy = (a.vy ?? 0) + fy
    }
    if (!b._manual) {
      b.vx = (b.vx ?? 0) - fx
      b.vy = (b.vy ?? 0) - fy
    }
  }

  for (const id of ids) {
    const position = positions[id]
    if (position._manual) {
      continue
    }
    const targetY = options.gravityTargets?.[id] ?? centerY
    position.vx = (position.vx ?? 0) + (centerX - position.x) * 0.0005
    position.vy = (position.vy ?? 0) + (targetY - position.y) * 0.004
    position.x += (position.vx ?? 0) * dt
    position.y += (position.vy ?? 0) * dt
  }
}

interface ScenarioClusterProps {
  scenarios: UIScenario[]
  selectedId: string | null
  onSelect: (id: string) => void
  activePulses: Set<string>
  pinnedIds: Set<string>
  /** When provided, surfaces a hint in the empty state. */
  emptyHint?: string
}

export function ScenarioCluster({
  scenarios,
  selectedId,
  onSelect,
  activePulses,
  pinnedIds,
  emptyHint,
}: ScenarioClusterProps) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 800, h: 520 })
  const posRef = useRef<Record<string, ClusterPosition>>({})
  const edgesRef = useRef<ScenarioEdge[]>([])
  const [, forceTick] = useState(0)
  const [view, setView] = useState({ x: 0, y: 0, scale: 1 })
  const dragRef = useRef<{ id: string; moved: boolean } | null>(null)
  const previousScenariosRef = useRef<Map<string, UIScenario>>(new Map())
  const [exitingScenarios, setExitingScenarios] = useState<Record<string, { scenario: UIScenario; expiresAt: number }>>({})

  useEffect(() => {
    const element = wrapRef.current
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

  useEffect(() => {
    const now = Date.now()
    const currentIds = new Set(scenarios.map((scenario) => scenario.id))
    setExitingScenarios((previous) => {
      const next: Record<string, { scenario: UIScenario; expiresAt: number }> = {}
      for (const [id, item] of Object.entries(previous)) {
        if (item.expiresAt > now && !currentIds.has(id)) {
          next[id] = item
        }
      }
      for (const [id, scenario] of previousScenariosRef.current.entries()) {
        if (!currentIds.has(id)) {
          next[id] = { scenario: fadingScenario(scenario, now), expiresAt: now + EXIT_RETENTION_MS }
        }
      }
      return next
    })
    previousScenariosRef.current = new Map(scenarios.map((scenario) => [scenario.id, scenario]))
  }, [scenarios])

  useEffect(() => {
    if (!Object.keys(exitingScenarios).length) {
      return
    }
    const timer = window.setTimeout(() => {
      const now = Date.now()
      setExitingScenarios((previous) => {
        const next = Object.fromEntries(Object.entries(previous).filter(([, item]) => item.expiresAt > now))
        return next
      })
    }, EXIT_RETENTION_MS + 80)
    return () => window.clearTimeout(timer)
  }, [exitingScenarios])

  const displayScenarios = useMemo(() => {
    const currentIds = new Set(scenarios.map((scenario) => scenario.id))
    return [
      ...scenarios,
      ...Object.values(exitingScenarios)
        .map((item) => item.scenario)
        .filter((scenario) => !currentIds.has(scenario.id)),
    ]
  }, [exitingScenarios, scenarios])

  useEffect(() => {
    edgesRef.current = computeEdges(displayScenarios)
    const previous = posRef.current
    const fresh = initialLayout(displayScenarios, size.w || 800, size.h || 520)
    for (const id of Object.keys(fresh)) {
      if (previous[id]) {
        fresh[id] = { ...fresh[id], ...previous[id] }
      }
    }
    posRef.current = fresh
    forceTick((value) => value + 1)
  }, [displayScenarios, size.h, size.w])

  const gravityTargets = useMemo(
    () => Object.fromEntries(displayScenarios.map((scenario) => [scenario.id, scenarioTargetY(scenario, size.h || 520)])),
    [displayScenarios, size.h],
  )

  useEffect(() => {
    let frame = 0
    let last = performance.now()
    const tick = (time: number) => {
      const dt = Math.min(0.05, (time - last) / 1000)
      last = time
      tickForces(posRef.current, edgesRef.current, { width: size.w, height: size.h, dt: dt * 60, gravityTargets })
      forceTick((value) => value + 1)
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [gravityTargets, size.h, size.w])

  useEffect(() => {
    const element = wrapRef.current
    if (!element) {
      return
    }
    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      if (event.ctrlKey || event.metaKey || Math.abs(event.deltaY) > 40) {
        const rect = element.getBoundingClientRect()
        const px = event.clientX - rect.left
        const py = event.clientY - rect.top
        setView((current) => {
          const nextScale = Math.max(0.3, Math.min(2.5, current.scale * Math.exp(-event.deltaY * 0.006)))
          const factor = nextScale / current.scale
          return { x: px - (px - current.x) * factor, y: py - (py - current.y) * factor, scale: nextScale }
        })
      } else {
        setView((current) => ({ ...current, x: current.x - event.deltaX, y: current.y - event.deltaY }))
      }
    }
    let panning: { x: number; y: number } | null = null
    const onPointerDown = (event: PointerEvent) => {
      if ((event.target as HTMLElement).closest('[data-cluster-node]')) {
        return
      }
      panning = { x: event.clientX, y: event.clientY }
      element.style.cursor = 'grabbing'
    }
    const onPointerMove = (event: PointerEvent) => {
      if (!panning) {
        return
      }
      const dx = event.clientX - panning.x
      const dy = event.clientY - panning.y
      panning = { x: event.clientX, y: event.clientY }
      setView((current) => ({ ...current, x: current.x + dx, y: current.y + dy }))
    }
    const onPointerUp = () => {
      panning = null
      element.style.cursor = 'grab'
    }
    element.addEventListener('wheel', onWheel, { passive: false })
    element.addEventListener('pointerdown', onPointerDown)
    window.addEventListener('pointermove', onPointerMove)
    window.addEventListener('pointerup', onPointerUp)
    return () => {
      element.removeEventListener('wheel', onWheel)
      element.removeEventListener('pointerdown', onPointerDown)
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', onPointerUp)
    }
  }, [])

  const onNodePointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>, id: string) => {
      event.stopPropagation()
      const startClient = { x: event.clientX, y: event.clientY }
      const startPos = { ...posRef.current[id] }
      const startView = view
      dragRef.current = { id, moved: false }
      const move = (pointerEvent: PointerEvent) => {
        const dx = (pointerEvent.clientX - startClient.x) / startView.scale
        const dy = (pointerEvent.clientY - startClient.y) / startView.scale
        const position = posRef.current[id]
        position.x = startPos.x + dx
        position.y = startPos.y + dy
        position.vx = 0
        position.vy = 0
        position._manual = true
        if (Math.abs(dx) + Math.abs(dy) > 3 && dragRef.current) {
          dragRef.current.moved = true
        }
        forceTick((value) => value + 1)
      }
      const up = () => {
        window.removeEventListener('pointermove', move)
        window.removeEventListener('pointerup', up)
        if (dragRef.current && !dragRef.current.moved) {
          onSelect(id)
        }
        dragRef.current = null
      }
      window.addEventListener('pointermove', move)
      window.addEventListener('pointerup', up)
    },
    [onSelect, view],
  )

  const highlight = useMemo(() => {
    if (!selectedId) {
      return new Set<string>()
    }
    const result = new Set<string>([selectedId])
    // ``edgesRef.current`` is a ref set synchronously by the earlier effect
    // when ``scenarios`` changes; recomputing on ``scenarios`` keeps the
    // highlight in sync without depending on the ref itself.
    for (const edge of edgesRef.current) {
      if (edge.from === selectedId) {
        result.add(edge.to)
      }
      if (edge.to === selectedId) {
        result.add(edge.from)
      }
    }
    return result
    // ``scenarios`` drives ``edgesRef.current`` via the effect above; we need
    // to recompute when it changes even though the linter doesn't see the
    // indirect dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, displayScenarios])

  useEffect(() => {
    if (!selectedId) {
      return
    }
    const position = posRef.current[selectedId]
    if (!position) {
      return
    }
    setView((current) => ({
      ...current,
      x: size.w / 2 - position.x * current.scale,
      y: size.h / 2 - position.y * current.scale,
    }))
  }, [selectedId, size.h, size.w])

  const positions = posRef.current

  if (!displayScenarios.length) {
    return (
      <div
        ref={wrapRef}
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--fg-4)',
          fontSize: 12,
          fontFamily: 'var(--font-mono)',
          padding: 24,
          textAlign: 'center',
        }}
      >
        {emptyHint ?? 'No scenarios yet — the daemon will populate this cluster as it ponders.'}
      </div>
    )
  }

  return (
    <div ref={wrapRef} style={{ position: 'relative', width: '100%', height: '100%', overflow: 'hidden', cursor: 'grab' }}>
      <svg width={size.w} height={size.h} style={{ position: 'absolute', inset: 0, pointerEvents: 'none', opacity: 0.35 }}>
        <defs>
          <pattern id="cluster-grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M 40 0 L 0 0 0 40" fill="none" stroke="var(--line-hair)" strokeWidth="0.5" />
          </pattern>
        </defs>
        <rect width={size.w} height={size.h} fill="url(#cluster-grid)" />
      </svg>

      <div
        style={{
          position: 'absolute',
          inset: 0,
          transform: `translate3d(${view.x}px, ${view.y}px, 0) scale(${view.scale})`,
          transformOrigin: '0 0',
        }}
      >
        <svg width={size.w} height={size.h} style={{ position: 'absolute', inset: 0, overflow: 'visible' }}>
          {edgesRef.current.map((edge) => {
            const from = positions[edge.from]
            const to = positions[edge.to]
            if (!from || !to) {
              return null
            }
            const highlighted = selectedId && (edge.from === selectedId || edge.to === selectedId)
            const dashed = edge.kind === 'shared-path'
            const strokeWidth = edge.kind === 'parent' ? 1.3 : Math.max(0.6, 1 + edge.weight * 1.6)
            const opacity = highlighted ? 0.95 : 0.3 + edge.weight * 0.4
            const color = highlighted ? 'var(--accent)' : edge.kind === 'parent' ? 'var(--line-2)' : 'var(--accent-soft)'
            return (
              <line
                key={`${edge.from}-${edge.to}-${edge.kind}`}
                x1={from.x}
                y1={from.y}
                x2={to.x}
                y2={to.y}
                stroke={color}
                strokeWidth={strokeWidth}
                strokeDasharray={dashed ? '3 4' : undefined}
                opacity={opacity}
              />
            )
          })}
        </svg>

        {displayScenarios.map((scenario) => {
          const position = positions[scenario.id]
          if (!position) {
            return null
          }
          const plan = isPlanScenario(scenario)
          const prediction = isPredictionScenario(scenario)
          const color = plan ? 'var(--accent)' : KIND_COLOR[scenario.kind]
          const radius = 17 * scenarioReadinessScale(scenario)
          const selected = selectedId === scenario.id
          const pulse = activePulses.has(scenario.id)
          const chosen = scenario.decisionState === 'chosen'
          const rejected = scenario.decisionState === 'rejected'
          const pinned = pinnedIds.has(scenario.id) || plan
          const dim = Boolean(selectedId && !highlight.has(scenario.id))
          const freshnessOpacity = scenarioFreshnessOpacity(scenario)
          const borderWidth = plan || prediction ? 2 : 1 + Math.max(0.4, scenario.confidence) * 1.4
          const stalePinned = pinned && (scenario.freshness === 'stale' || scenario.relevance < 0.65)
          const exiting = scenario.visibility === 'archived' || scenario.lifecycleMotion === 'fading'
          const motionStyle = scenarioMotionMarkerStyle(color, scenario.lifecycleMotion)

          return (
            <div
              key={scenario.id}
              data-cluster-node
              data-scenario-id={scenario.id}
              title={`${scenario.title}\nRelevance ${Math.round(scenario.relevance * 100)}% · ${scenario.readiness} · ${scenario.visibilityReason || scenario.freshness}`}
              onPointerDown={(event) => {
                if (exiting) {
                  event.stopPropagation()
                  return
                }
                onNodePointerDown(event, scenario.id)
              }}
              style={{
                position: 'absolute',
                left: position.x - radius,
                top: position.y - radius,
                width: radius * 2,
                height: radius * 2,
                borderRadius: plan ? '28%' : '50%',
                cursor: exiting ? 'default' : 'pointer',
                background: rejected ? 'transparent' : `color-mix(in oklch, ${color} ${plan || prediction || scenario.readiness === 'ready' ? 30 : 18}%, transparent)`,
                border: `${borderWidth}px solid ${color}`,
                opacity: rejected ? 0.25 : exiting ? 0.24 : dim ? 0.42 : freshnessOpacity,
                boxShadow: selected
                  ? `0 0 0 3px var(--bg-0), 0 0 0 5px ${color}, 0 0 26px ${color}80`
                  : pinned
                    ? `0 0 0 2px var(--bg-0), 0 0 0 4px var(--amber)`
                  : prediction
                    ? `0 0 18px ${color}55`
                  : chosen
                    ? `0 0 18px ${color}80`
                    : 'none',
                transition: 'box-shadow .2s, opacity .65s',
                animation: pulse ? 'dc-pulse 1.2s infinite' : 'none',
              }}
            >
              {chosen ? <div style={{ position: 'absolute', inset: '30%', borderRadius: '50%', background: color }} /> : null}
              {pinned ? (
                <div
                  aria-label={stalePinned ? 'pinned stale' : 'pinned'}
                  style={{
                    position: 'absolute',
                    top: -8,
                    right: -8,
                    width: 12,
                    height: 12,
                    borderRadius: '50%',
                    background: stalePinned ? 'var(--bg-2)' : 'var(--amber)',
                    border: '2px solid var(--bg-0)',
                    boxShadow: stalePinned ? 'inset 0 0 0 2px var(--amber)' : 'none',
                  }}
                />
              ) : null}
              <div
                className="mono"
                style={{
                  position: 'absolute',
                  top: '110%',
                  left: '50%',
                  transform: 'translateX(-50%)',
                  whiteSpace: 'normal',
                  fontSize: 9.5,
                  lineHeight: 1.18,
                  color: selected || prediction ? 'var(--fg-1)' : 'var(--fg-3)',
                  marginTop: 4,
                  pointerEvents: 'none',
                  width: selected ? 260 : 210,
                  textAlign: 'center',
                  overflow: 'hidden',
                  display: '-webkit-box',
                  WebkitLineClamp: selected ? 3 : 2,
                  WebkitBoxOrient: 'vertical',
                }}
              >
                {scenario.title}
              </div>
              <div
                className="mono"
                style={{
                  position: 'absolute',
                  bottom: '110%',
                  left: '50%',
                  transform: 'translateX(-50%)',
                  whiteSpace: 'nowrap',
                  fontSize: 9.5,
                  color,
                  marginBottom: 3,
                  pointerEvents: 'none',
                  fontWeight: 500,
                }}
              >
                {scenarioNodeBadge(scenario)}
              </div>
              {motionStyle ? <div aria-hidden style={motionStyle} /> : null}
            </div>
          )
        })}
      </div>

      <ScenarioLegend />

      <div
        style={{
          position: 'absolute',
          bottom: 12,
          right: 12,
          display: 'flex',
          gap: 2,
          background: 'var(--bg-1)',
          border: '1px solid var(--line-1)',
          borderRadius: 'var(--r-2)',
          padding: 2,
          zIndex: 3,
        }}
      >
        <button onClick={() => setView((current) => ({ ...current, scale: Math.max(0.3, current.scale * 0.83) }))} style={clusterBtn} aria-label="zoom out">
          −
        </button>
        <button onClick={() => setView((current) => ({ ...current, scale: Math.min(2.5, current.scale * 1.2) }))} style={clusterBtn} aria-label="zoom in">
          +
        </button>
        <button onClick={() => setView({ x: 0, y: 0, scale: 1 })} style={{ ...clusterBtn, paddingLeft: 8, paddingRight: 8 }} aria-label="reset view">
          ⟲
        </button>
        <span className="mono" style={{ alignSelf: 'center', padding: '0 8px', fontSize: 10.5, color: 'var(--fg-3)' }}>
          {Math.round(view.scale * 100)}%
        </span>
      </div>
    </div>
  )
}

function ScenarioLegend() {
  return (
    <div style={legendStyle} aria-label="Scenario map legend">
      <div className="mono" style={{ color: 'var(--fg-4)', fontSize: 10, letterSpacing: 1 }}>
        LEGEND
      </div>
      <div style={legendGridStyle}>
        {LEGEND_KINDS.map((kind) => (
          <div key={kind} style={legendItemStyle}>
            <span style={{ ...legendBubbleStyle, borderColor: KIND_COLOR[kind], background: `color-mix(in oklch, ${KIND_COLOR[kind]} 26%, transparent)` }} />
            <span>{kind}</span>
          </div>
        ))}
      </div>
      <div style={legendItemStyle}>
        <span style={{ ...legendBubbleStyle, borderRadius: '28%', borderColor: 'var(--accent)', background: 'color-mix(in oklch, var(--accent) 28%, transparent)' }} />
        <span>plan</span>
        <span style={{ ...legendBubbleStyle, borderColor: 'var(--kind-change)', background: 'color-mix(in oklch, var(--kind-change) 28%, transparent)', boxShadow: '0 0 14px var(--kind-change)' }} />
        <span>prepared/live work</span>
      </div>
    </div>
  )
}

const legendStyle: React.CSSProperties = {
  position: 'absolute',
  bottom: 12,
  left: 12,
  zIndex: 3,
  display: 'grid',
  gap: 8,
  width: 390,
  maxWidth: 'calc(100% - 36px)',
  background: 'color-mix(in oklch, var(--bg-1) 94%, transparent)',
  border: '1px solid var(--line-1)',
  borderRadius: 'var(--r-2)',
  padding: '10px 11px',
  boxShadow: '0 10px 28px rgba(0,0,0,.2)',
  color: 'var(--fg-3)',
  fontSize: 10.5,
}

const legendGridStyle: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'repeat(5, minmax(0, 1fr))',
  gap: 7,
}

const legendItemStyle: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 5,
  minWidth: 0,
  fontFamily: 'var(--font-mono)',
  whiteSpace: 'nowrap',
}

const legendBubbleStyle: React.CSSProperties = {
  display: 'inline-flex',
  width: 10,
  height: 10,
  flex: '0 0 auto',
  borderRadius: '50%',
  border: '1.5px solid var(--accent)',
}

const clusterBtn: React.CSSProperties = {
  background: 'var(--bg-2)',
  border: 'none',
  color: 'var(--fg-1)',
  padding: '5px 10px',
  fontFamily: 'var(--font-mono)',
  fontSize: 13,
  cursor: 'pointer',
  borderRadius: 'var(--r-1)',
  minWidth: 28,
}
