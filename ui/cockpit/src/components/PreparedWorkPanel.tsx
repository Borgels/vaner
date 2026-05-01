import { useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react'

import { inspectWorkProduct, listPreparedWork, runPreparedWorkAction } from '../api/client'
import type {
  PreparedWorkAction,
  PreparedWorkCard,
  WorkProductInspection,
} from '../types'

export interface PreparedWorkPanelProps {
  baseUrl?: string
  intervalMs?: number
  limit?: number
  includeAdvisory?: boolean
  fetcher?: typeof fetch
  onAction?: (message: string) => void
}

function actionLabel(action: PreparedWorkAction): string {
  if (action.kind === 'feedback') return 'Useful'
  return action.label
}

function canRenderAction(action: PreparedWorkAction | null): action is PreparedWorkAction {
  return Boolean(action?.endpoint)
}

export function PreparedWorkPanel({
  baseUrl = '',
  intervalMs = 4000,
  limit = 6,
  includeAdvisory = false,
  fetcher,
  onAction,
}: PreparedWorkPanelProps) {
  const [cards, setCards] = useState<PreparedWorkCard[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [detail, setDetail] = useState<WorkProductInspection | null>(null)
  const [detailFallback, setDetailFallback] = useState<string | null>(null)
  // Lazy-loaded self-eval / lifecycle data, keyed by work-product source_id.
  // Populated for cards backed by a work_product when the panel mounts and
  // reused for the inline confidence bars + the inspect detail view.
  const [inspectionsBySource, setInspectionsBySource] = useState<
    Record<string, WorkProductInspection | null>
  >({})
  const endpoint = useMemo(() => {
    const query = new URLSearchParams()
    query.set('surface', 'cockpit')
    query.set('limit', String(limit))
    if (includeAdvisory) query.set('include_advisory', 'true')
    return `${baseUrl}/prepared-work?${query.toString()}`
  }, [baseUrl, includeAdvisory, limit])

  const refresh = useCallback(async () => {
    try {
      if (fetcher) {
        const response = await fetcher(endpoint)
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const data = await response.json()
        setCards(Array.isArray(data.prepared_work) ? data.prepared_work : [])
      } else {
        const data = await listPreparedWork({ limit, includeAdvisory, surface: 'cockpit' })
        setCards(data.prepared_work)
      }
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [endpoint, fetcher, includeAdvisory, limit])

  useEffect(() => {
    let cancelled = false
    const doFetch = async () => {
      if (!cancelled) await refresh()
    }
    void doFetch()
    const handle = window.setInterval(doFetch, intervalMs)
    return () => {
      cancelled = true
      window.clearInterval(handle)
    }
  }, [intervalMs, refresh])

  // Lazy-fetch self_eval + lifecycle for any work-product-backed card we
  // haven't inspected yet. Cards backed by predictions don't have a
  // /work-products/{id}/inspect endpoint, so we skip them.
  useEffect(() => {
    let cancelled = false
    const targets = cards.filter(
      (card) => card.source_type === 'work_product' && inspectionsBySource[card.source_id] === undefined,
    )
    if (targets.length === 0) return
    void Promise.all(
      targets.map(async (card) => {
        try {
          const data = await inspectWorkProduct(card.source_id)
          if (!cancelled) {
            setInspectionsBySource((prev) => ({ ...prev, [card.source_id]: data }))
          }
        } catch {
          if (!cancelled) {
            setInspectionsBySource((prev) => ({ ...prev, [card.source_id]: null }))
          }
        }
      }),
    )
    return () => {
      cancelled = true
    }
  }, [cards, inspectionsBySource])

  const runAction = useCallback(
    async (card: PreparedWorkCard, action: PreparedWorkAction) => {
      if (!action.endpoint) return
      try {
        const result = fetcher
          ? await (async () => {
              const method = action.kind === 'inspect' ? 'GET' : 'POST'
              const body = action.kind === 'feedback' ? JSON.stringify({ feedback_state: 'useful' }) : undefined
              const response = await fetcher(`${baseUrl}${action.endpoint}`, {
                method,
                headers: body ? { 'content-type': 'application/json' } : undefined,
                body,
              })
              if (!response.ok) throw new Error(`HTTP ${response.status}`)
              return response.json()
            })()
          : await runPreparedWorkAction(action.endpoint, action.kind)
        if (action.kind === 'inspect') {
          const inspection = result as WorkProductInspection
          setDetail(inspection)
          setDetailFallback(null)
          if (card.source_type === 'work_product' && inspection?.source_id) {
            setInspectionsBySource((prev) => ({ ...prev, [card.source_id]: inspection }))
          }
        } else if (action.kind === 'export') {
          setDetail(null)
          setDetailFallback(JSON.stringify(result, null, 2))
        }
        if (action.kind === 'dismiss') {
          setCards((current) => current.filter((item) => item.id !== card.id))
        }
        onAction?.(`${action.label} complete`)
      } catch (err) {
        onAction?.(err instanceof Error ? err.message : `Failed to ${action.label.toLowerCase()}`)
      }
    },
    [baseUrl, fetcher, onAction],
  )

  if (loading && cards.length === 0) {
    return (
      <section aria-label="Prepared work" style={panelStyle}>
        <PanelHeader />
        <p style={emptyStyle}>Loading…</p>
      </section>
    )
  }

  if (error) {
    return (
      <section aria-label="Prepared work" style={panelStyle}>
        <PanelHeader />
        <p role="alert" style={emptyStyle}>Error: {error}</p>
      </section>
    )
  }

  return (
    <section aria-label="Prepared work" style={panelStyle}>
      <PanelHeader count={cards.length} />
      <div className="scroll" style={{ overflow: 'auto', minHeight: 0, padding: '0 12px 12px' }}>
        {cards.length === 0 ? <p style={emptyStyle}>No prepared work is ready.</p> : null}
        {cards.map((card) => (
          <PreparedWorkItem
            key={card.id}
            card={card}
            inspection={
              card.source_type === 'work_product' ? inspectionsBySource[card.source_id] ?? null : null
            }
            onAction={runAction}
          />
        ))}
        {detail ? (
          <PreparedWorkInspectionDetail
            inspection={detail}
            onClose={() => setDetail(null)}
          />
        ) : null}
        {detailFallback ? (
          <pre style={detailStyle} aria-label="Prepared work detail">
            {detailFallback}
          </pre>
        ) : null}
      </div>
    </section>
  )
}

function PreparedWorkItem({
  card,
  inspection,
  onAction,
}: {
  card: PreparedWorkCard
  inspection: WorkProductInspection | null
  onAction: (card: PreparedWorkCard, action: PreparedWorkAction) => Promise<void>
}) {
  const primary = canRenderAction(card.primary_action) ? card.primary_action : null
  return (
    <article style={cardStyle}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10 }}>
        <div style={{ minWidth: 0 }}>
          <div style={titleStyle}>{card.title}</div>
          <div style={summaryStyle}>{card.summary}</div>
        </div>
        <span style={badgeStyle}>{card.badge}</span>
      </div>
      <div style={factsStyle}>
        <span>{card.confidence_label}</span>
        <span>{card.freshness_label}</span>
        <span>{card.target_label}</span>
        {card.evidence_count ? <span>{card.evidence_count} sources</span> : null}
      </div>
      {inspection?.self_eval ? <SelfEvalBars selfEval={inspection.self_eval} /> : null}
      <div style={actionsStyle}>
        {primary ? (
          <button type="button" style={primaryButtonStyle} onClick={() => void onAction(card, primary)}>
            {actionLabel(primary)}
          </button>
        ) : null}
        {card.secondary_actions.filter(canRenderAction).slice(0, 3).map((action) => (
          <button key={`${card.id}-${action.kind}`} type="button" style={secondaryButtonStyle} onClick={() => void onAction(card, action)}>
            {actionLabel(action)}
          </button>
        ))}
      </div>
    </article>
  )
}

function clamp01(value: number): number {
  if (Number.isNaN(value)) return 0
  if (value < 0) return 0
  if (value > 1) return 1
  return value
}

function SelfEvalBars({ selfEval }: { selfEval: NonNullable<WorkProductInspection['self_eval']> }) {
  // Three side-by-side micro-bars. Contradiction is inverted so 'longer is
  // better' for all three — a quick visual sanity check rather than a
  // numerical breakdown.
  const bars = [
    {
      key: 'evidence',
      label: 'evidence',
      value: clamp01(selfEval.evidence_coverage),
      color: '#5eb2ff',
      title: `evidence coverage ${selfEval.evidence_coverage.toFixed(2)}`,
    },
    {
      key: 'grounded',
      label: 'grounded',
      value: clamp01(selfEval.groundedness),
      color: '#6cc76c',
      title: `groundedness ${selfEval.groundedness.toFixed(2)}`,
    },
    {
      key: 'contradict',
      label: 'contradict',
      value: clamp01(1 - selfEval.contradiction_risk),
      color: selfEval.contradiction_risk >= 0.35 ? '#a96666' : '#a36cc7',
      title: `contradiction risk ${selfEval.contradiction_risk.toFixed(2)} (lower is better)`,
    },
  ]
  return (
    <div
      data-testid="self-eval-bars"
      style={{ display: 'flex', gap: 6, marginTop: 8, alignItems: 'baseline' }}
    >
      {bars.map((bar) => (
        <div
          key={bar.key}
          title={bar.title}
          style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 2 }}
        >
          <span
            style={{
              fontFamily: 'var(--font-mono, monospace)',
              fontSize: 9.5,
              color: 'var(--fg-4)',
              letterSpacing: 0.4,
            }}
          >
            {bar.label}
          </span>
          <span
            role="img"
            aria-label={bar.title}
            style={{
              display: 'block',
              height: 5,
              borderRadius: 3,
              background: 'var(--bg-0, #0e0e12)',
              overflow: 'hidden',
              position: 'relative',
            }}
          >
            <span
              style={{
                display: 'block',
                height: '100%',
                width: `${Math.round(bar.value * 100)}%`,
                background: bar.color,
                transition: 'width 240ms ease',
              }}
            />
          </span>
        </div>
      ))}
    </div>
  )
}

function lifecycleEventLabel(event_type: string): string {
  switch (event_type) {
    case 'inspect':
      return 'INSPECTED'
    case 'export':
      return 'EXPORTED'
    case 'dismiss':
      return 'DISMISSED'
    case 'feedback':
      return 'FEEDBACK'
    default:
      return event_type.toUpperCase()
  }
}

function lifecycleAge(timestamp: number): string {
  if (!timestamp) return ''
  const dt = Math.max(0, Date.now() / 1000 - timestamp)
  if (dt < 60) return `${Math.round(dt)}s`
  if (dt < 3600) return `${Math.round(dt / 60)}m`
  if (dt < 86400) return `${Math.round(dt / 3600)}h`
  return `${Math.round(dt / 86400)}d`
}

function PreparedWorkInspectionDetail({
  inspection,
  onClose,
}: {
  inspection: WorkProductInspection
  onClose: () => void
}) {
  const events = (inspection.events ?? []).slice(0, 6).reverse()
  return (
    <div
      style={{
        ...detailStyle,
        whiteSpace: 'normal',
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
      }}
      aria-label="Prepared work detail"
      data-testid="prepared-work-inspection"
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
        <strong style={{ color: 'var(--fg-1)', fontSize: 12 }}>{inspection.title}</strong>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close prepared work detail"
          style={{ ...secondaryButtonStyle, padding: '0 6px' }}
        >
          x
        </button>
      </div>
      {inspection.why_prepared ? (
        <p style={{ margin: 0, fontSize: 11.5, color: 'var(--fg-3)' }}>{inspection.why_prepared}</p>
      ) : null}
      {inspection.self_eval ? <SelfEvalBars selfEval={inspection.self_eval} /> : null}
      {events.length > 0 ? (
        <div
          style={{
            display: 'flex',
            gap: 6,
            flexWrap: 'wrap',
            fontFamily: 'var(--font-mono, monospace)',
            fontSize: 10.5,
            color: 'var(--fg-3)',
          }}
          aria-label="Lifecycle events"
        >
          {events.map((evt, idx) => (
            <span key={`${evt.event_type}-${evt.timestamp}-${idx}`}>
              {lifecycleEventLabel(evt.event_type)} {lifecycleAge(evt.timestamp)} ago
              {idx < events.length - 1 ? ' →' : ''}
            </span>
          ))}
        </div>
      ) : null}
      {inspection.body ? (
        <pre
          style={{
            margin: 0,
            padding: 8,
            border: '1px solid var(--line-hair)',
            borderRadius: 'var(--r-2)',
            background: 'var(--bg-0)',
            color: 'var(--fg-2)',
            whiteSpace: 'pre-wrap',
            fontSize: 11,
            maxHeight: 160,
            overflow: 'auto',
          }}
        >
          {inspection.body}
        </pre>
      ) : null}
    </div>
  )
}

function PanelHeader({ count }: { count?: number }) {
  return (
    <header style={headerStyle}>
      <span>PREPARED WORK</span>
      <span style={{ color: 'var(--fg-4)' }}>{typeof count === 'number' ? count : ''}</span>
    </header>
  )
}

const panelStyle: CSSProperties = {
  minHeight: 0,
  display: 'flex',
  flexDirection: 'column',
  background: 'var(--bg-1)',
  borderBottom: '1px solid var(--line-1)',
}

const headerStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  padding: '10px 12px',
  color: 'var(--fg-3)',
  fontFamily: 'var(--font-mono)',
  fontSize: 10,
  letterSpacing: 1.1,
}

const emptyStyle: CSSProperties = {
  margin: 0,
  padding: '0 12px 12px',
  color: 'var(--fg-4)',
  fontSize: 12,
}

const cardStyle: CSSProperties = {
  border: '1px solid var(--line-hair)',
  borderRadius: 'var(--r-2)',
  background: 'var(--bg-inset)',
  padding: 10,
  marginBottom: 8,
}

const titleStyle: CSSProperties = {
  color: 'var(--fg-1)',
  fontSize: 12.5,
  lineHeight: 1.25,
  fontWeight: 600,
}

const summaryStyle: CSSProperties = {
  color: 'var(--fg-3)',
  fontSize: 11.5,
  lineHeight: 1.35,
  marginTop: 4,
}

const badgeStyle: CSSProperties = {
  border: '1px solid var(--line-2)',
  borderRadius: 999,
  color: 'var(--fg-2)',
  fontSize: 10,
  padding: '2px 7px',
  whiteSpace: 'nowrap',
}

const factsStyle: CSSProperties = {
  display: 'flex',
  gap: 7,
  flexWrap: 'wrap',
  color: 'var(--fg-4)',
  fontFamily: 'var(--font-mono)',
  fontSize: 10,
  marginTop: 8,
}

const actionsStyle: CSSProperties = {
  display: 'flex',
  flexWrap: 'wrap',
  gap: 6,
  marginTop: 9,
}

const primaryButtonStyle: CSSProperties = {
  border: '1px solid var(--accent)',
  borderRadius: 'var(--r-1)',
  background: 'var(--accent-bg)',
  color: 'var(--fg-1)',
  padding: '5px 8px',
  fontSize: 11,
  cursor: 'pointer',
}

const secondaryButtonStyle: CSSProperties = {
  border: '1px solid var(--line-2)',
  borderRadius: 'var(--r-1)',
  background: 'transparent',
  color: 'var(--fg-2)',
  padding: '5px 8px',
  fontSize: 11,
  cursor: 'pointer',
}

const detailStyle: CSSProperties = {
  maxHeight: 160,
  overflow: 'auto',
  border: '1px solid var(--line-hair)',
  borderRadius: 'var(--r-2)',
  background: 'var(--bg-0)',
  color: 'var(--fg-2)',
  padding: 10,
  fontSize: 10.5,
  whiteSpace: 'pre-wrap',
}
