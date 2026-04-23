import { useEffect, useMemo, useState } from 'react'

/**
 * Phase 4 / Phase D (cockpit parity): renders the active PredictedPrompt
 * list exposed by the daemon at /predictions/active.
 *
 * This component owns a small polling loop (2s cadence) against the HTTP
 * endpoint rather than subscribing to the SSE predictions stage, so it can
 * be dropped into any cockpit layout without touching the shared pipeline
 * events hook.
 */

export type ReadinessState =
  | 'queued'
  | 'grounding'
  | 'evidence_gathering'
  | 'drafting'
  | 'ready'
  | 'stale'

export type HypothesisType = 'likely_next' | 'possible_branch' | 'long_tail'

export interface PredictionRow {
  id: string
  spec: {
    label: string
    description: string
    source: string
    anchor: string
    confidence: number
    hypothesis_type: HypothesisType
    specificity: 'concrete' | 'category' | 'anchor'
  }
  run: {
    weight: number
    token_budget: number
    tokens_used: number
    model_calls: number
    scenarios_spawned: number
    scenarios_complete: number
    readiness: ReadinessState
  }
  artifacts: {
    evidence_score: number
    has_draft: boolean
    has_briefing: boolean
  }
}

export interface ActivePredictionsPanelProps {
  /** Base URL for the Vaner daemon HTTP surface. */
  baseUrl?: string
  /** Polling interval in milliseconds. Defaults to 2000. */
  intervalMs?: number
  /** When a row is clicked, invoke this callback with the prediction id. */
  onAdopt?: (predictionId: string) => void
  /** Optional fetch override (for tests). */
  fetcher?: typeof fetch
}

const READINESS_COLORS: Record<ReadinessState, string> = {
  queued: 'var(--fg-3)',
  grounding: 'var(--accent-blue)',
  evidence_gathering: 'var(--accent-teal)',
  drafting: 'var(--accent-violet)',
  ready: 'var(--accent-green)',
  stale: 'var(--fg-4)',
}

const HYPOTHESIS_PREFIX: Record<HypothesisType, string> = {
  likely_next: 'Next step:',
  possible_branch: 'Vaner is exploring:',
  long_tail: 'Might follow:',
}

function renderLabel(row: PredictionRow): string {
  const prefix = HYPOTHESIS_PREFIX[row.spec.hypothesis_type] ?? ''
  return prefix ? `${prefix} ${row.spec.label}` : row.spec.label
}

function isAdoptable(state: ReadinessState): boolean {
  return state === 'ready' || state === 'drafting'
}

export function ActivePredictionsPanel({
  baseUrl = '',
  intervalMs = 2000,
  onAdopt,
  fetcher,
}: ActivePredictionsPanelProps) {
  const [rows, setRows] = useState<PredictionRow[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState<boolean>(true)
  const endpoint = useMemo(() => `${baseUrl}/predictions/active`, [baseUrl])

  useEffect(() => {
    let cancelled = false
    const doFetch = async () => {
      try {
        const f = fetcher ?? fetch
        const response = await f(endpoint)
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }
        const data = await response.json()
        if (cancelled) return
        setRows(Array.isArray(data.predictions) ? data.predictions : [])
        setError(null)
      } catch (err) {
        if (cancelled) return
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    doFetch()
    const handle = window.setInterval(doFetch, intervalMs)
    return () => {
      cancelled = true
      window.clearInterval(handle)
    }
  }, [endpoint, intervalMs, fetcher])

  if (loading && rows.length === 0) {
    return (
      <section aria-label="Active predictions" className="active-predictions">
        <header>Active predictions</header>
        <p>Loading…</p>
      </section>
    )
  }

  if (error) {
    return (
      <section aria-label="Active predictions" className="active-predictions">
        <header>Active predictions</header>
        <p role="alert">Error: {error}</p>
      </section>
    )
  }

  if (rows.length === 0) {
    return (
      <section aria-label="Active predictions" className="active-predictions">
        <header>Active predictions</header>
        <p>No active predictions yet — Vaner hasn't enrolled any for this cycle.</p>
      </section>
    )
  }

  return (
    <section aria-label="Active predictions" className="active-predictions">
      <header>Active predictions</header>
      <ul>
        {rows.map((row) => {
          const pct =
            row.run.token_budget > 0
              ? Math.min(100, Math.round((row.run.tokens_used / row.run.token_budget) * 100))
              : 0
          const readinessColor = READINESS_COLORS[row.run.readiness] ?? 'var(--fg-3)'
          const adoptable = isAdoptable(row.run.readiness)
          return (
            <li key={row.id} data-prediction-id={row.id} data-readiness={row.run.readiness}>
              <button
                type="button"
                disabled={!adoptable}
                onClick={() => onAdopt?.(row.id)}
                aria-label={`Adopt ${row.spec.label}`}
              >
                <span className="label">{renderLabel(row)}</span>
                <span className="readiness" style={{ color: readinessColor }}>
                  {row.run.readiness}
                </span>
                <span className="source">{row.spec.source}</span>
                <span className="progress" aria-label={`${pct}% of token budget used`}>
                  {pct}%
                </span>
              </button>
              <div className="description">{row.spec.description}</div>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
