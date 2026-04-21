import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { KIND_COLOR } from '../lib/constants'
import type { UIScenario } from '../types'

type Position = { x: number; y: number; _manual?: boolean; _baseY?: number }

export function computeLayout(scenarios: UIScenario[], width: number, height: number): Record<string, Position> {
  const byId = Object.fromEntries(scenarios.map((scenario) => [scenario.id, { ...scenario, children: [] as UIScenario[] }]))
  for (const scenario of Object.values(byId)) {
    if (scenario.parent && byId[scenario.parent]) {
      byId[scenario.parent].children.push(scenario)
    }
  }

  const roots = scenarios.filter((scenario) => !scenario.parent)
  const columnWidth = Math.max(width / 5, 160)
  const positions: Record<string, Position> = {}

  const walk = (node: (typeof byId)[string], depth: number, yCenter: number, yBand: number) => {
    const x = columnWidth * (depth + 0.6)
    positions[node.id] = { x, y: yCenter }

    const children = node.children.sort((a, b) => b.score - a.score)
    if (!children.length) {
      return
    }

    const band = Math.max(yBand, 90)
    const slice = band / children.length
    children.forEach((child, index) => {
      const y = yCenter - band / 2 + slice * (index + 0.5)
      walk(byId[child.id], depth + 1, y, slice * 1.8)
    })
  }

  const totalHeight = height - 80
  roots.forEach((root, index) => {
    const yStart = 60 + (totalHeight / (roots.length + 0.4)) * (index + 0.5)
    walk(byId[root.id], 0, yStart, (totalHeight / Math.max(roots.length, 1)) * 0.95)
  })

  scenarios.forEach((scenario) => {
    if (!positions[scenario.id]) {
      positions[scenario.id] = { x: columnWidth * 0.6, y: height - 60 }
    }
  })

  return positions
}

interface FrontierGraphProps {
  scenarios: UIScenario[]
  selectedId: string | null
  onSelect: (id: string) => void
  activePulses: Set<string>
  pinnedIds: Set<string>
}

export function FrontierGraph({ scenarios, selectedId, onSelect, activePulses, pinnedIds }: FrontierGraphProps) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 800, h: 600 })
  const [view, setView] = useState({ x: 0, y: 0, scale: 1 })
  const posRef = useRef<Record<string, Position>>({})
  const [, forceTick] = useState(0)
  const dragRef = useRef<{ id: string; moved: boolean } | null>(null)

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
    const fresh = computeLayout(scenarios, size.w, size.h)
    const previous = posRef.current
    for (const id of Object.keys(fresh)) {
      if (previous[id]?._manual) {
        fresh[id] = { ...fresh[id], x: previous[id].x, y: previous[id].y, _manual: true }
      }
    }
    posRef.current = fresh
    forceTick((value) => value + 1)
  }, [scenarios, size.h, size.w])

  useEffect(() => {
    let frame = 0
    const start = performance.now()

    const tick = (time: number) => {
      const delta = (time - start) / 1000
      const positions = posRef.current

      for (const id of Object.keys(positions)) {
        if (positions[id]._manual) {
          continue
        }
        if (!positions[id]._baseY) {
          positions[id]._baseY = positions[id].y
        }
        const phase = (id.charCodeAt(4) % 10) * 0.5
        positions[id].y = positions[id]._baseY + Math.sin(delta * 0.6 + phase) * 2.2
      }

      forceTick((value) => value + 1)
      frame = requestAnimationFrame(tick)
    }

    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [])

  useEffect(() => {
    const element = wrapRef.current
    if (!element) {
      return
    }

    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      if ((event.ctrlKey || event.metaKey || (Math.abs(event.deltaY) > 40 && Number.isInteger(event.deltaY)))) {
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
      if ((event.target as HTMLElement).closest('[data-node]')) {
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
      const startView = view
      const startClient = { x: event.clientX, y: event.clientY }
      const startPosition = { ...posRef.current[id] }
      dragRef.current = { id, moved: false }

      const move = (pointerEvent: PointerEvent) => {
        const dx = (pointerEvent.clientX - startClient.x) / startView.scale
        const dy = (pointerEvent.clientY - startClient.y) / startView.scale
        const position = posRef.current[id]
        position.x = startPosition.x + dx
        position.y = startPosition.y + dy
        position._manual = true
        position._baseY = position.y
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

  const highlightPath = useMemo(() => {
    if (!selectedId) {
      return new Set<string>()
    }

    const result = new Set([selectedId])
    const byId = Object.fromEntries(scenarios.map((scenario) => [scenario.id, scenario]))
    let current = byId[selectedId]
    while (current?.parent) {
      result.add(current.parent)
      current = byId[current.parent]
    }

    const addChildren = (id: string) => {
      scenarios.filter((scenario) => scenario.parent === id).forEach((child) => {
        result.add(child.id)
        addChildren(child.id)
      })
    }
    addChildren(selectedId)

    return result
  }, [scenarios, selectedId])

  const positions = posRef.current
  const edges = scenarios
    .filter((scenario) => scenario.parent && positions[scenario.parent])
    .map((scenario) => ({ from: positions[scenario.parent!], to: positions[scenario.id], scenario }))

  const radius = (scenario: UIScenario) => 8 + scenario.score * 14

  return (
    <div ref={wrapRef} style={{ position: 'relative', width: '100%', height: '100%', overflow: 'hidden', cursor: 'grab' }}>
      <svg width={size.w} height={size.h} style={{ position: 'absolute', inset: 0, pointerEvents: 'none', opacity: 0.4 }}>
        <defs>
          <pattern id="grid-ig" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M 40 0 L 0 0 0 40" fill="none" stroke="var(--line-hair)" strokeWidth="0.5" />
          </pattern>
        </defs>
        <rect width={size.w} height={size.h} fill="url(#grid-ig)" />
      </svg>

      <div
        style={{
          position: 'absolute',
          top: 12,
          left: 0,
          right: 0,
          display: 'flex',
          justifyContent: 'space-around',
          pointerEvents: 'none',
          zIndex: 2,
        }}
      >
        {['D0 ROOT', 'D1', 'D2', 'D3', 'D4 LEAF'].map((label) => (
          <div key={label} className="mono" style={{ fontSize: 9.5, letterSpacing: 1.2, color: 'var(--fg-4)' }}>
            {label}
          </div>
        ))}
      </div>

      <div
        style={{
          position: 'absolute',
          inset: 0,
          transform: `translate3d(${view.x}px, ${view.y}px, 0) scale(${view.scale})`,
          transformOrigin: '0 0',
        }}
      >
        <svg width={size.w} height={size.h} style={{ position: 'absolute', inset: 0, overflow: 'visible' }}>
          {edges.map(({ from, to, scenario }) => {
            const midpointX = (from.x + to.x) / 2
            const d = `M ${from.x} ${from.y} C ${midpointX} ${from.y}, ${midpointX} ${to.y}, ${to.x} ${to.y}`
            const onPath = highlightPath.has(scenario.id) && highlightPath.has(scenario.parent!)
            const pulsing = activePulses.has(scenario.id) || activePulses.has(scenario.parent!)
            return (
              <g key={scenario.id}>
                <path d={d} fill="none" stroke={onPath ? 'var(--accent)' : 'var(--line-2)'} strokeWidth={onPath ? 1.5 : 1} opacity={onPath ? 0.9 : 0.55} />
                {pulsing ? (
                  <path d={d} fill="none" stroke={KIND_COLOR[scenario.kind]} strokeWidth="1.5" strokeDasharray="4 4" opacity="0.9" style={{ animation: 'dc-flow 0.9s linear infinite' }} />
                ) : null}
              </g>
            )
          })}
        </svg>

        {scenarios.map((scenario) => {
          const position = positions[scenario.id]
          if (!position) {
            return null
          }

          const r = radius(scenario)
          const selected = selectedId === scenario.id
          const pulse = activePulses.has(scenario.id)
          const color = KIND_COLOR[scenario.kind]
          const rejected = scenario.decisionState === 'rejected'
          const chosen = scenario.decisionState === 'chosen'
          const pinned = pinnedIds.has(scenario.id)
          const dim = Boolean(selectedId && !highlightPath.has(scenario.id))

          return (
            <div
              key={scenario.id}
              data-node
              onPointerDown={(event) => onNodePointerDown(event, scenario.id)}
              style={{
                position: 'absolute',
                left: position.x - r,
                top: position.y - r,
                width: r * 2,
                height: r * 2,
                borderRadius: '50%',
                cursor: 'pointer',
                background: rejected ? 'transparent' : `color-mix(in oklch, ${color} 18%, transparent)`,
                border: `1.5px solid ${color}`,
                opacity: rejected ? 0.35 : dim ? 0.35 : 1,
                boxShadow: selected
                  ? `0 0 0 3px var(--bg-0), 0 0 0 5px ${color}, 0 0 26px ${color}80`
                  : chosen
                    ? `0 0 18px ${color}80`
                    : 'none',
                transition: 'box-shadow .2s, opacity .2s',
                animation: pulse ? 'dc-pulse 1.2s infinite' : 'none',
              }}
            >
              {chosen ? <div style={{ position: 'absolute', inset: '30%', borderRadius: '50%', background: color }} /> : null}
              {pinned ? (
                <div
                  style={{
                    position: 'absolute',
                    top: -8,
                    right: -8,
                    width: 12,
                    height: 12,
                    borderRadius: '50%',
                    background: 'var(--amber)',
                    border: '2px solid var(--bg-0)',
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
                  whiteSpace: 'nowrap',
                  fontSize: 9.5,
                  color: selected ? 'var(--fg-1)' : 'var(--fg-3)',
                  marginTop: 4,
                  pointerEvents: 'none',
                  maxWidth: 160,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                {scenario.title.length > 22 ? `${scenario.title.slice(0, 20)}…` : scenario.title}
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
                {scenario.score.toFixed(3)}
              </div>
            </div>
          )
        })}
      </div>

      <div
        style={{
          position: 'absolute',
          bottom: 16,
          right: 16,
          display: 'flex',
          gap: 2,
          background: 'var(--bg-1)',
          border: '1px solid var(--line-1)',
          borderRadius: 'var(--r-2)',
          padding: 2,
          zIndex: 3,
        }}
      >
        <button onClick={() => setView((current) => ({ ...current, scale: Math.min(2.5, current.scale * 1.2) }))} style={ctrlBtn}>
          +
        </button>
        <button onClick={() => setView((current) => ({ ...current, scale: Math.max(0.3, current.scale * 0.83) }))} style={ctrlBtn}>
          −
        </button>
        <button onClick={() => setView({ x: 0, y: 0, scale: 1 })} style={{ ...ctrlBtn, paddingLeft: 8, paddingRight: 8 }}>
          ⟲
        </button>
        <span className="mono" style={{ alignSelf: 'center', padding: '0 8px', fontSize: 10.5, color: 'var(--fg-3)' }}>
          {Math.round(view.scale * 100)}%
        </span>
      </div>
    </div>
  )
}

const ctrlBtn: React.CSSProperties = {
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
