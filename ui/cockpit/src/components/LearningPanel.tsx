// SPDX-License-Identifier: Apache-2.0
//
// Cockpit refresh — feedback → learning loop.
// Small left-rail panel that closes the loop visibly: shows recent
// feedback_events the user has sent and a one-line summary of the
// learning_state keys the engine maintains. Read-only, polled.

import { useCallback, useEffect, useMemo, useState } from 'react'

import { getLearningRecent } from '../api/client'
import type { FeedbackEvent } from '../types'

const REFRESH_INTERVAL_MS = 12000

function cardStyle(): React.CSSProperties {
  return {
    border: '1px solid var(--line-1)',
    borderRadius: 6,
    background: 'var(--bg-1, #18181c)',
    padding: '12px 14px',
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
    color: 'var(--fg-1, #f0f0f0)',
  }
}

function relTime(epoch: number | undefined | null): string {
  if (!epoch) return '—'
  const now = Date.now() / 1000
  const dt = Math.max(0, now - Number(epoch))
  if (dt < 60) return `${Math.round(dt)}s ago`
  if (dt < 3600) return `${Math.round(dt / 60)}m ago`
  if (dt < 86400) return `${Math.round(dt / 3600)}h ago`
  return `${Math.round(dt / 86400)}d ago`
}

function feedbackChipStyle(kind: string | undefined): React.CSSProperties {
  const palette: Record<string, string> = {
    useful: '#6cc76c',
    partial: 'var(--amber, #e6b656)',
    wrong: '#a96666',
    irrelevant: 'var(--fg-3, #9a9aa2)',
    not_useful: '#a96666',
  }
  const color = palette[kind ?? ''] ?? 'var(--fg-3, #9a9aa2)'
  return {
    fontSize: 10,
    color,
    border: `1px solid ${color}`,
    borderRadius: 999,
    padding: '0 6px',
    fontFamily: 'var(--font-mono, monospace)',
    letterSpacing: 0.4,
    textTransform: 'uppercase',
  }
}

function feedbackKind(event: FeedbackEvent): string {
  if (event.feedback_kind) return event.feedback_kind
  // The store persists the feedback kind inside metadata_json; we tolerate
  // either shape so the panel works against unmigrated rows.
  if (event.metadata_json) {
    try {
      const parsed = JSON.parse(event.metadata_json) as Record<string, unknown>
      const kind = parsed.feedback_kind ?? parsed.kind ?? parsed.outcome
      if (typeof kind === 'string') return kind
    } catch {
      // ignore — fall through
    }
  }
  return 'feedback'
}

function feedbackTitle(event: FeedbackEvent): string {
  if (event.scenario_id) return event.scenario_id
  if (event.query_id) return event.query_id
  return event.id ?? 'event'
}

function summarizeLearningKey(key: string, value: unknown): string | null {
  if (value === null || value === undefined) return null
  if (typeof value === 'object' && !Array.isArray(value)) {
    const entries = Object.entries(value as Record<string, unknown>)
    if (entries.length === 0) return null
    if (key === 'skill_weights' || key === 'scenario_kind_priors' || key === 'intent_priors') {
      const sorted = entries
        .filter(([, v]) => typeof v === 'number')
        .sort((a, b) => Math.abs(Number(b[1])) - Math.abs(Number(a[1])))
        .slice(0, 2)
      if (sorted.length === 0) return `${entries.length} entries`
      return sorted
        .map(([k, v]) => `${k} ${(Number(v) >= 0 ? '+' : '')}${Number(v).toFixed(2)}`)
        .join(', ')
    }
    return `${entries.length} entries`
  }
  if (Array.isArray(value)) return `${value.length} entries`
  return String(value)
}

export interface LearningPanelProps {
  /** Override polling interval for tests. */
  refreshIntervalMs?: number
  /** Override max feedback rows shown. */
  limit?: number
}

export function LearningPanel({
  refreshIntervalMs = REFRESH_INTERVAL_MS,
  limit = 5,
}: LearningPanelProps = {}) {
  const [events, setEvents] = useState<FeedbackEvent[]>([])
  const [learning, setLearning] = useState<Record<string, unknown>>({})
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const resp = await getLearningRecent(limit)
      setEvents(resp.feedback_events.slice(0, limit))
      setLearning(resp.learning_state)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoaded(true)
    }
  }, [limit])

  useEffect(() => {
    void refresh()
    const handle = window.setInterval(() => void refresh(), refreshIntervalMs)
    return () => window.clearInterval(handle)
  }, [refresh, refreshIntervalMs])

  const summaries = useMemo(() => {
    const out: Array<{ key: string; summary: string }> = []
    for (const [key, value] of Object.entries(learning)) {
      const summary = summarizeLearningKey(key, value)
      if (summary) out.push({ key, summary })
    }
    return out.slice(0, 4)
  }, [learning])

  return (
    <section
      aria-labelledby="learning-panel-heading"
      role="region"
      style={cardStyle()}
      data-testid="learning-panel"
    >
      <h3
        id="learning-panel-heading"
        style={{ margin: 0, fontSize: 12.5, fontWeight: 600 }}
      >
        What Vaner learned
      </h3>
      {error ? (
        <p
          role="alert"
          style={{ margin: 0, fontSize: 11.5, color: 'var(--amber, #e6b656)' }}
        >
          {error}
        </p>
      ) : null}
      {loaded && events.length === 0 && summaries.length === 0 ? (
        <p style={{ margin: 0, fontSize: 11.5, color: 'var(--fg-3, #9a9aa2)' }}>
          No feedback recorded yet — use <code>vaner.feedback</code> to teach.
        </p>
      ) : null}
      {events.length > 0 ? (
        <ul
          aria-label="Recent feedback"
          style={{
            margin: 0,
            padding: 0,
            listStyle: 'none',
            display: 'flex',
            flexDirection: 'column',
            gap: 4,
          }}
        >
          {events.map((event, idx) => (
            <li
              key={event.id ?? `${event.timestamp ?? idx}`}
              style={{
                display: 'flex',
                alignItems: 'baseline',
                gap: 6,
                fontSize: 11.5,
                color: 'var(--fg-2, #d0d0d6)',
                minWidth: 0,
              }}
            >
              <span style={feedbackChipStyle(feedbackKind(event))}>{feedbackKind(event)}</span>
              <span
                style={{
                  flex: 1,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {feedbackTitle(event)}
              </span>
              <span style={{ fontSize: 10.5, color: 'var(--fg-3, #9a9aa2)' }}>
                {relTime(event.timestamp)}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {summaries.length > 0 ? (
        <ul
          aria-label="Learning state summary"
          style={{
            margin: 0,
            padding: 0,
            listStyle: 'none',
            borderTop: events.length > 0 ? '1px solid var(--line-2, #2a2a30)' : 'none',
            paddingTop: events.length > 0 ? 6 : 0,
            display: 'flex',
            flexDirection: 'column',
            gap: 2,
          }}
        >
          {summaries.map(({ key, summary }) => (
            <li
              key={key}
              style={{
                fontSize: 11,
                color: 'var(--fg-3, #9a9aa2)',
                fontFamily: 'var(--font-mono, monospace)',
              }}
            >
              <span style={{ color: 'var(--fg-2, #d0d0d6)' }}>{key}</span>: {summary}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  )
}
