import { useEffect, useMemo, useRef, useState } from 'react'

import type { PipelineEvent } from '../api/usePipelineEvents'
import type { PipelineStage, UIEvent } from '../types'

export type StreamEvent = PipelineEvent | UIEvent

const STAGE_COLORS: Record<PipelineStage, string> = {
  signals: 'var(--fg-3)',
  targets: 'var(--accent-soft)',
  model: 'var(--amber)',
  artefacts: 'var(--kind-refactor)',
  scenarios: 'var(--accent)',
  decisions: 'var(--kind-research)',
  system: 'var(--fg-4)',
}

const STAGE_ORDER: PipelineStage[] = [
  'signals',
  'targets',
  'model',
  'artefacts',
  'scenarios',
  'decisions',
  'system',
]

function resolveStage(event: StreamEvent): PipelineStage | null {
  if ('stage' in event && event.stage) {
    return event.stage
  }
  return null
}

export interface EventStreamPanelProps {
  title: string
  subtitle: string
  events: StreamEvent[]
  onSelect: (id: string) => void
  live: boolean
  /** When true, subsequent heartbeat events collapse into a single row. */
  collapseHeartbeats?: boolean
  /** Optional count of in-flight LLM requests to render a spinner in the header. */
  pendingLlm?: number
}

export function EventStreamPanel({
  title,
  subtitle,
  events,
  onSelect,
  live,
  collapseHeartbeats = true,
  pendingLlm = 0,
}: EventStreamPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [filter, setFilter] = useState<Set<PipelineStage>>(new Set())

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = 0
    }
  }, [events.length])

  const filtered = useMemo(() => {
    if (!filter.size) {
      return events
    }
    return events.filter((event) => {
      const stage = resolveStage(event)
      return stage ? filter.has(stage) : false
    })
  }, [events, filter])

  const rows = useMemo(() => {
    if (!collapseHeartbeats) {
      return filtered.map((event) => ({ event, collapsedCount: 0 }))
    }
    const out: Array<{ event: StreamEvent; collapsedCount: number }> = []
    let cluster: { event: StreamEvent; collapsedCount: number } | null = null
    for (const event of filtered) {
      const isHeartbeat = (event.tag === 'keepalive' || event.kind === 'cycle.start' || event.kind === 'cycle.end') && !event.scn
      if (isHeartbeat && cluster && cluster.event.tag === event.tag) {
        cluster.collapsedCount += 1
        continue
      }
      cluster = { event, collapsedCount: 0 }
      out.push(cluster)
    }
    return out
  }, [filtered, collapseHeartbeats])

  const available = useMemo(() => {
    const present = new Set<PipelineStage>()
    for (const event of events) {
      const stage = resolveStage(event)
      if (stage) {
        present.add(stage)
      }
    }
    return STAGE_ORDER.filter((stage) => present.has(stage))
  }, [events])

  return (
    <div
      style={{
        borderLeft: '1px solid var(--line-1)',
        background: 'var(--bg-1)',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        height: '100%',
      }}
    >
      <div
        style={{
          padding: '12px 16px 10px',
          borderBottom: '1px solid var(--line-hair)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 10,
        }}
      >
        <div style={{ minWidth: 0 }}>
          <div className="mono" style={{ fontSize: 9.5, letterSpacing: 1.2, color: 'var(--fg-4)' }}>
            {title}
          </div>
          <div style={{ fontSize: 13, color: 'var(--fg-1)', marginTop: 2, fontFamily: 'var(--font-display)' }}>
            {subtitle}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {pendingLlm > 0 ? (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: 'var(--amber)' }}>
              <span
                aria-hidden
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  border: '1.5px solid var(--amber)',
                  borderTopColor: 'transparent',
                  animation: 'dc-spin 0.8s linear infinite',
                }}
              />
              <span className="mono" style={{ fontSize: 9.5 }}>llm×{pendingLlm}</span>
            </span>
          ) : null}
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: live ? 'var(--ok)' : 'var(--fg-4)',
                boxShadow: live ? '0 0 8px var(--ok)' : 'none',
              }}
            />
            <span className="mono" style={{ fontSize: 10, color: 'var(--fg-3)' }}>
              {live ? 'tailing' : 'waiting'}
            </span>
          </span>
        </div>
      </div>

      {available.length ? (
        <div
          role="toolbar"
          aria-label="Filter event stream by stage"
          style={{
            display: 'flex',
            gap: 4,
            padding: '8px 12px',
            borderBottom: '1px solid var(--line-hair)',
            overflowX: 'auto',
          }}
        >
          <FilterChip
            label="all"
            active={filter.size === 0}
            color="var(--fg-3)"
            onClick={() => setFilter(new Set())}
          />
          {available.map((stage) => (
            <FilterChip
              key={stage}
              label={stage}
              active={filter.has(stage)}
              color={STAGE_COLORS[stage]}
              onClick={() =>
                setFilter((prev) => {
                  const next = new Set(prev)
                  if (next.has(stage)) {
                    next.delete(stage)
                  } else {
                    next.add(stage)
                  }
                  return next
                })
              }
            />
          ))}
        </div>
      ) : null}

      <div
        ref={scrollRef}
        className="scroll"
        style={{ flex: 1, overflow: 'auto', fontFamily: 'var(--font-mono)', fontSize: 11, lineHeight: 1.5 }}
      >
        {rows.map(({ event, collapsedCount }, index) => {
          const stage = resolveStage(event)
          const color = stage ? STAGE_COLORS[stage] : event.color
          return (
            <button
              key={event.id}
              type="button"
              onClick={() => (event.scn ? onSelect(event.scn) : undefined)}
              style={{
                width: '100%',
                textAlign: 'left',
                padding: '6px 12px 6px 16px',
                borderBottom: '1px solid var(--line-hair)',
                borderLeft: `2px solid ${color}`,
                background: index === 0 ? 'color-mix(in oklch, var(--accent) 7%, transparent)' : 'transparent',
                color: 'var(--fg-1)',
                cursor: event.scn ? 'pointer' : 'default',
                display: 'grid',
                gridTemplateColumns: 'auto auto 1fr auto',
                alignItems: 'baseline',
                gap: 8,
                fontFamily: 'var(--font-mono)',
                fontSize: 11,
              }}
              aria-label={`${stage ?? event.tag} event ${event.msg}`}
            >
              <span style={{ color: 'var(--fg-4)', fontSize: 10 }}>{event.t}</span>
              <span
                style={{
                  color,
                  textTransform: 'uppercase',
                  letterSpacing: 0.6,
                  fontSize: 9.5,
                  minWidth: 64,
                }}
              >
                {stage ?? event.tag}
              </span>
              <span style={{ color: 'var(--fg-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {event.msg}
              </span>
              {collapsedCount > 0 ? (
                <span className="mono" style={{ fontSize: 9.5, color: 'var(--fg-4)' }}>
                  ×{collapsedCount + 1}
                </span>
              ) : event.scn ? (
                <span className="mono" style={{ fontSize: 9.5, color: 'var(--fg-4)' }}>
                  ↗
                </span>
              ) : (
                <span />
              )}
            </button>
          )
        })}
        {!rows.length ? (
          <div style={{ padding: 18, color: 'var(--fg-4)', fontSize: 12 }}>
            {filter.size ? 'No events match the active filter.' : 'No events yet.'}
          </div>
        ) : null}
      </div>
    </div>
  )
}

interface FilterChipProps {
  label: string
  active: boolean
  color: string
  onClick: () => void
}

function FilterChip({ label, active, color, onClick }: FilterChipProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="mono"
      aria-pressed={active}
      style={{
        padding: '3px 9px',
        borderRadius: 'var(--r-1)',
        border: `1px solid ${active ? color : 'var(--line-1)'}`,
        background: active ? `color-mix(in oklch, ${color} 18%, transparent)` : 'transparent',
        color: active ? 'var(--fg-1)' : 'var(--fg-3)',
        fontSize: 10,
        letterSpacing: 0.6,
        textTransform: 'uppercase',
        cursor: 'pointer',
      }}
    >
      {label}
    </button>
  )
}
