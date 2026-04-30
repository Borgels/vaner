import { useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react'

import { listPreparedWork, runPreparedWorkAction } from '../api/client'
import type { PreparedWorkAction, PreparedWorkCard } from '../types'

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
  const [detail, setDetail] = useState<string | null>(null)
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
        if (action.kind === 'inspect' || action.kind === 'export') {
          setDetail(JSON.stringify(result, null, 2))
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
          <PreparedWorkItem key={card.id} card={card} onAction={runAction} />
        ))}
        {detail ? (
          <pre style={detailStyle} aria-label="Prepared work detail">
            {detail}
          </pre>
        ) : null}
      </div>
    </section>
  )
}

function PreparedWorkItem({
  card,
  onAction,
}: {
  card: PreparedWorkCard
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
