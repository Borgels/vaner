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
  label?: string
  display_label?: string
  source_label?: string
  readiness?: ReadinessState
  readiness_label?: string
  adoptable?: boolean
  match_state?: 'strong_match' | 'weak_match' | 'unrelated' | 'stale' | string
  match_reason?: string
  recommended_action?: 'adopt' | 'inspect' | 'ignore' | string
  snapshot_freshness?: 'ready' | 'warming' | 'stale' | 'cold' | string
  confidence?: number
  token_budget?: number
  tokens_used?: number
  ui_summary?: string
  spec?: {
    label: string
    description: string
    source: string
    anchor: string
    confidence: number
    hypothesis_type: HypothesisType
    specificity: 'concrete' | 'category' | 'anchor'
  }
  run?: {
    weight: number
    token_budget: number
    tokens_used: number
    model_calls: number
    scenarios_spawned: number
    scenarios_complete: number
    readiness: ReadinessState
  }
  artifacts?: {
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
  /**
   * Cockpit refresh: when true (the default) the panel asks the daemon
   * for all in-flight predictions grouped by readiness, and renders a
   * lane per state instead of a flat list. Set to false to retain the
   * pre-refresh ready-only rendering.
   */
  showAllStates?: boolean
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
  if (row.display_label || row.label) return row.display_label ?? row.label ?? row.id
  const prefix = HYPOTHESIS_PREFIX[row.spec?.hypothesis_type ?? 'likely_next'] ?? ''
  const label = row.spec?.label ?? row.id
  return prefix ? `${prefix} ${label}` : label
}

function rowReadiness(row: PredictionRow): ReadinessState {
  return (row.readiness ?? row.run?.readiness ?? 'queued') as ReadinessState
}

function isAdoptable(row: PredictionRow): boolean {
  if (row.recommended_action === 'adopt') return true
  if (row.recommended_action === 'ignore') return false
  if (row.adoptable === false) return false
  const state = rowReadiness(row)
  return state === 'ready' || state === 'drafting'
}

function tokenBudget(row: PredictionRow): number {
  return row.token_budget ?? row.run?.token_budget ?? 0
}

function tokensUsed(row: PredictionRow): number {
  return row.tokens_used ?? row.run?.tokens_used ?? 0
}

const PIPELINE_LANES: ReadinessState[] = [
  'queued',
  'grounding',
  'evidence_gathering',
  'drafting',
  'ready',
]

export function ActivePredictionsPanel({
  baseUrl = '',
  intervalMs = 2000,
  onAdopt,
  fetcher,
  showAllStates = true,
}: ActivePredictionsPanelProps) {
  const [rows, setRows] = useState<PredictionRow[]>([])
  const [byState, setByState] = useState<Record<string, PredictionRow[]>>({})
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState<boolean>(true)
  const endpoint = useMemo(
    () => `${baseUrl}/predictions/active${showAllStates ? '?include_all=true' : ''}`,
    [baseUrl, showAllStates],
  )

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
        const grouped = data.by_state && typeof data.by_state === 'object' ? data.by_state : {}
        const cleaned: Record<string, PredictionRow[]> = {}
        for (const [state, items] of Object.entries(grouped)) {
          if (Array.isArray(items)) cleaned[state] = items as PredictionRow[]
        }
        setByState(cleaned)
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
      <section aria-label="Prepared context" className="active-predictions">
        <header>Prepared context</header>
        <p>Loading…</p>
      </section>
    )
  }

  if (error) {
    return (
      <section aria-label="Prepared context" className="active-predictions">
        <header>Prepared context</header>
        <p role="alert">Error: {error}</p>
      </section>
    )
  }

  const hasGrouped = showAllStates && Object.keys(byState).length > 0
  const totalGrouped = hasGrouped
    ? Object.values(byState).reduce((sum, list) => sum + list.length, 0)
    : 0

  if (rows.length === 0 && totalGrouped === 0) {
    return (
      <section aria-label="Prepared context" className="active-predictions">
        <header>Prepared context</header>
        <p>No prepared context is ready for this turn yet.</p>
      </section>
    )
  }

  if (hasGrouped) {
    return (
      <section aria-label="Prepared context pipeline" className="active-predictions">
        <header>Prepared context pipeline</header>
        <div className="pipeline-lanes" role="list">
          {PIPELINE_LANES.map((state) => {
            const items = byState[state] ?? []
            const color = READINESS_COLORS[state] ?? 'var(--fg-3)'
            return (
              <div
                key={state}
                role="listitem"
                className="pipeline-lane"
                data-readiness={state}
                data-testid={`prediction-lane-${state}`}
              >
                <div
                  className="pipeline-lane-header"
                  style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}
                >
                  <span style={{ color, fontFamily: 'var(--font-mono, monospace)', fontSize: 11 }}>
                    {state.replace(/_/g, ' ')}
                  </span>
                  <span style={{ fontSize: 11, color: 'var(--fg-3)' }}>{items.length}</span>
                </div>
                <ul style={{ margin: '4px 0 0', padding: 0, listStyle: 'none' }}>
                  {items.slice(0, 3).map((row) => {
                    const adoptable = isAdoptable(row)
                    return (
                      <li
                        key={row.id}
                        data-prediction-id={row.id}
                        style={{ fontSize: 12, padding: '2px 0' }}
                      >
                        <button
                          type="button"
                          disabled={!adoptable}
                          onClick={() => onAdopt?.(row.id)}
                          aria-label={`Use ${renderLabel(row)}`}
                          style={{
                            all: 'unset',
                            cursor: adoptable ? 'pointer' : 'default',
                            color: adoptable ? 'var(--accent, #5eb2ff)' : 'var(--fg-2)',
                            display: 'block',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {renderLabel(row)}
                        </button>
                      </li>
                    )
                  })}
                  {items.length > 3 ? (
                    <li style={{ fontSize: 11, color: 'var(--fg-3)' }}>+{items.length - 3} more</li>
                  ) : null}
                </ul>
              </div>
            )
          })}
        </div>
      </section>
    )
  }

  return (
    <section aria-label="Prepared context" className="active-predictions">
      <header>Prepared context</header>
      <ul>
        {rows.map((row) => {
          const budget = tokenBudget(row)
          const pct = budget > 0 ? Math.min(100, Math.round((tokensUsed(row) / budget) * 100)) : 0
          const readiness = rowReadiness(row)
          const readinessColor = READINESS_COLORS[readiness] ?? 'var(--fg-3)'
          const adoptable = isAdoptable(row)
          return (
            <li key={row.id} data-prediction-id={row.id} data-readiness={readiness}>
              <button
                type="button"
                disabled={!adoptable}
                onClick={() => onAdopt?.(row.id)}
                aria-label={`Use ${renderLabel(row)}`}
              >
                <span className="label">{renderLabel(row)}</span>
                <span className="readiness" style={{ color: readinessColor }}>
                  {row.snapshot_freshness ?? row.readiness_label ?? readiness}
                </span>
                <span className="source">{row.source_label ?? row.spec?.source ?? 'prepared context'}</span>
                <span className="progress" aria-label={`${pct}% of token budget used`}>
                  {pct}%
                </span>
              </button>
              <div className="description">{row.match_reason ?? row.spec?.description ?? row.ui_summary ?? ''}</div>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
