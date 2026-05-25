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
  cards?: PreparedWorkCard[]
  loading?: boolean
  error?: string | null
  selectedId?: string | null
  onSelect?: (id: string) => void
  onCardsChange?: (cards: PreparedWorkCard[]) => void
  variant?: 'rail' | 'main'
}

function actionLabel(action: PreparedWorkAction): string {
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
  cards: controlledCards,
  loading: controlledLoading,
  error: controlledError,
  selectedId,
  onSelect,
  onCardsChange,
  variant = 'rail',
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

  const displayCards = controlledCards ?? cards
  const displayError = controlledError ?? error
  const displayLoading = controlledLoading ?? loading
  const setDisplayCards = useCallback(
    (next: PreparedWorkCard[]) => {
      if (controlledCards !== undefined) {
        onCardsChange?.(next)
      } else {
        setCards(next)
      }
    },
    [controlledCards, onCardsChange],
  )

  const refresh = useCallback(async () => {
    if (controlledCards !== undefined) {
      return
    }
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
  }, [controlledCards, endpoint, fetcher, includeAdvisory, limit])

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
    const targets = displayCards.filter(
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
  }, [displayCards, inspectionsBySource])

  const runAction = useCallback(
    async (card: PreparedWorkCard, action: PreparedWorkAction) => {
      if (!action.endpoint) return
      try {
        const result = fetcher
          ? await (async () => {
              const method = action.kind === 'inspect' ? 'GET' : 'POST'
              const feedbackState =
                typeof action.arguments?.feedback_state === 'string' ? action.arguments.feedback_state : 'useful'
              const body = action.kind === 'feedback' ? JSON.stringify({ feedback_state: feedbackState }) : undefined
              const response = await fetcher(`${baseUrl}${action.endpoint}`, {
                method,
                headers: body ? { 'content-type': 'application/json' } : undefined,
                body,
              })
              if (!response.ok) throw new Error(`HTTP ${response.status}`)
              return response.json()
            })()
          : await runPreparedWorkAction(action)
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
          setDisplayCards(displayCards.filter((item) => item.id !== card.id))
        }
        onAction?.(`${action.label} complete`)
      } catch (err) {
        onAction?.(err instanceof Error ? err.message : `Failed to ${action.label.toLowerCase()}`)
      }
    },
    [baseUrl, displayCards, fetcher, onAction, setDisplayCards],
  )

  if (displayLoading && displayCards.length === 0) {
    return (
      <section aria-label="Prepared work" style={panelStyleForVariant(variant)}>
        <PanelHeader variant={variant} />
        <p style={emptyStyle}>Loading…</p>
      </section>
    )
  }

  if (displayError) {
    return (
      <section aria-label="Prepared work" style={panelStyleForVariant(variant)}>
        <PanelHeader variant={variant} />
        <p role="alert" style={emptyStyle}>Error: {displayError}</p>
      </section>
    )
  }

  return (
    <section aria-label="Prepared work" style={panelStyleForVariant(variant)}>
      {variant === 'rail' ? <PanelHeader count={displayCards.length} variant={variant} /> : null}
      <div className="scroll" style={{ overflow: 'auto', minHeight: 0, padding: variant === 'main' ? '0 18px 18px' : '0 12px 12px' }}>
        {displayCards.length === 0 ? <p style={emptyStyle}>No prepared work is ready.</p> : null}
        {displayCards.map((card) => (
          <PreparedWorkItem
            key={card.id}
            card={card}
            selected={selectedId === card.id}
            variant={variant}
            inspection={
              card.source_type === 'work_product' ? inspectionsBySource[card.source_id] ?? null : null
            }
            onSelect={onSelect}
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
  selected,
  variant,
  onSelect,
  onAction,
}: {
  card: PreparedWorkCard
  inspection: WorkProductInspection | null
  selected: boolean
  variant: 'rail' | 'main'
  onSelect?: (id: string) => void
  onAction: (card: PreparedWorkCard, action: PreparedWorkAction) => Promise<void>
}) {
  const primary = canRenderAction(card.primary_action) ? card.primary_action : null
  const secondaryActions = uniqueActions(card.secondary_actions.filter(canRenderAction), primary)
    .filter((action) => variant !== 'main' || action.kind !== 'feedback')
    .slice(0, variant === 'main' ? 2 : 3)
  const typeLabel = preparedKindLabel(card.kind)
  const title = displayTitle(card)
  const summary = displaySummary(card, title)
  const facts = displayFacts(card, variant)
  return (
    <article
      style={cardStyleForVariant(variant, selected)}
      onClick={() => onSelect?.(card.id)}
      data-selected={selected ? 'true' : undefined}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10 }}>
        <div style={{ minWidth: 0 }}>
          <div style={titleStyle}>{title}</div>
          {summary ? <div style={summaryStyle}>{summary}</div> : null}
        </div>
        <span style={badgeStyle}>{typeLabel}</span>
      </div>
      <div style={factsStyle}>
        {facts.map((fact) => <span key={fact}>{fact}</span>)}
      </div>
      {card.action_note ? <div style={warningStyle}>{card.action_note}</div> : null}
      {inspection?.self_eval ? <SelfEvalBars selfEval={inspection.self_eval} /> : null}
      <div style={actionsStyle}>
        {primary ? (
          <button type="button" style={primaryButtonStyle} onClick={() => void onAction(card, primary)}>
            {actionLabel(primary)}
          </button>
        ) : null}
        {secondaryActions.map((action) => (
          <button key={`${card.id}-${action.kind}-${action.label}`} type="button" style={secondaryButtonStyle} onClick={() => void onAction(card, action)}>
            {actionLabel(action)}
          </button>
        ))}
      </div>
    </article>
  )
}

function uniqueActions(actions: PreparedWorkAction[], primary: PreparedWorkAction | null): PreparedWorkAction[] {
  const seen = new Set<string>()
  if (primary) {
    seen.add(`${primary.kind}:${primary.endpoint ?? ''}:${primary.label}`)
  }
  return actions.filter((action) => {
    const key = `${action.kind}:${action.endpoint ?? ''}:${action.label}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function displayTitle(card: PreparedWorkCard): string {
  return card.title
    .replace(/^Continue related work:\s*/i, '')
    .replace(/^Prepare context for\s+/i, 'Context: ')
}

function displaySummary(card: PreparedWorkCard, title: string): string {
  const summary = card.summary.trim()
  if (!summary) return ''
  const normalizedTitle = title.toLowerCase().replace(/\W+/g, ' ').trim()
  const normalizedSummary = summary.toLowerCase().replace(/\W+/g, ' ').trim()
  if (normalizedTitle && normalizedSummary.includes(normalizedTitle.slice(0, Math.min(60, normalizedTitle.length)))) {
    return ''
  }
  if (summary.startsWith('Likely follow-up to the most recent user question:')) {
    return 'Likely next branch inferred from the current conversation.'
  }
  if (summary.startsWith('Artefact item under goal')) {
    return ''
  }
  return summary
}

function displayFacts(card: PreparedWorkCard, variant: 'rail' | 'main'): string[] {
  const facts = variant === 'rail' ? [card.badge, card.confidence_label, card.freshness_label] : [card.confidence_label, card.freshness_label]
  if (card.kind === 'finance' || card.external_input_count) {
    facts.push(card.external_input_count ? `${card.external_input_count} external` : 'external state')
  } else if (card.evidence_count) {
    facts.push(`${card.evidence_count} sources`)
  }
  if (variant === 'rail' && card.target_label) facts.push(card.target_label)
  if (card.sensitivity_class && card.sensitivity_class !== 'general') facts.push(card.sensitivity_class)
  if (card.fresh_precheck_required) facts.push('fresh precheck')
  return facts.filter(Boolean)
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
      {(inspection.action_note || inspection.sensitivity_class || inspection.fresh_precheck_required) ? (
        <div style={warningStyle}>
          {[inspection.action_note, inspection.sensitivity_class ? `sensitivity: ${inspection.sensitivity_class}` : '', inspection.fresh_precheck_required ? 'fresh precheck required' : '']
            .filter(Boolean)
            .join(' · ')}
        </div>
      ) : null}
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

export function preparedKindLabel(kind: PreparedWorkCard['kind']): string {
  switch (kind) {
    case 'review':
      return 'review note'
    case 'bug':
      return 'bug hypothesis'
    case 'docs':
      return 'docs drift'
    case 'diff':
      return 'virtual diff'
    case 'brief':
      return 'research brief'
    case 'finance':
      return 'trading prep'
    case 'draft':
      return 'suggested change'
    case 'prediction':
      return 'research brief'
    default:
      return String(kind)
  }
}

function PanelHeader({ count, variant }: { count?: number; variant: 'rail' | 'main' }) {
  return (
    <header style={headerStyle}>
      <span>{variant === 'main' ? 'What Vaner prepared' : 'PREPARED WORK'}</span>
      <span style={{ color: 'var(--fg-4)' }}>{typeof count === 'number' ? count : ''}</span>
    </header>
  )
}

function panelStyleForVariant(variant: 'rail' | 'main'): CSSProperties {
  return {
    minHeight: 0,
    height: '100%',
    display: 'flex',
    flexDirection: 'column',
    background: variant === 'main' ? 'transparent' : 'var(--bg-1)',
    borderBottom: variant === 'rail' ? '1px solid var(--line-1)' : 'none',
  }
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

function cardStyleForVariant(variant: 'rail' | 'main', selected: boolean): CSSProperties {
  return {
    border: selected ? '1px solid var(--accent)' : '1px solid var(--line-hair)',
    borderRadius: 'var(--r-1)',
    background: selected ? 'color-mix(in oklch, var(--accent) 8%, var(--bg-inset))' : 'var(--bg-inset)',
    padding: variant === 'main' ? 12 : 10,
    marginBottom: variant === 'main' ? 8 : 8,
    cursor: 'pointer',
  }
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

const warningStyle: CSSProperties = {
  marginTop: 8,
  color: 'var(--amber)',
  fontFamily: 'var(--font-mono)',
  fontSize: 10.5,
  lineHeight: 1.35,
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
