// SPDX-License-Identifier: Apache-2.0
//
// Cockpit refresh — Goals & artefacts.
// Read-only view: workspace_goals + linked intent_artefacts (latest snapshot
// + most recent reconciliation outcome). The MCP tools vaner.goals.declare /
// update_status / delete cover writes; the cockpit deliberately does not
// expose CRUD here so the surface stays small.

import { useCallback, useEffect, useMemo, useState } from 'react'

import { fetchArtefact, listArtefacts, listGoals } from '../api/client'
import type { Artefact, ArtefactDetail, Goal } from '../types'

const REFRESH_INTERVAL_MS = 8000

function cardStyle(): React.CSSProperties {
  return {
    border: '1px solid var(--line-1)',
    borderRadius: 6,
    background: 'var(--bg-1, #18181c)',
    padding: '14px 16px',
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
    color: 'var(--fg-1, #f0f0f0)',
  }
}

function pillStyle(status: string): React.CSSProperties {
  const palette: Record<string, string> = {
    active: 'var(--accent, #5eb2ff)',
    paused: 'var(--fg-3, #9a9aa2)',
    abandoned: '#a96666',
    achieved: '#6cc76c',
  }
  const color = palette[status] ?? 'var(--fg-3, #9a9aa2)'
  return {
    fontSize: 10.5,
    color,
    border: `1px solid ${color}`,
    borderRadius: 999,
    padding: '1px 8px',
    fontFamily: 'var(--font-mono, monospace)',
    letterSpacing: 0.4,
    textTransform: 'uppercase',
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

interface GoalRowProps {
  goal: Goal
  expanded: boolean
  onToggle: () => void
  artefactsByGoal: Record<string, Artefact[]>
  detailByArtefact: Record<string, ArtefactDetail | null>
  onLoadArtefact: (id: string) => void
}

function GoalRow({
  goal,
  expanded,
  onToggle,
  artefactsByGoal,
  detailByArtefact,
  onLoadArtefact,
}: GoalRowProps) {
  const artefacts = artefactsByGoal[goal.id] ?? []

  return (
    <li
      style={{
        listStyle: 'none',
        borderTop: '1px solid var(--line-2, #2a2a30)',
        paddingTop: 10,
      }}
      data-testid={`goal-row-${goal.id}`}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        style={{
          all: 'unset',
          cursor: 'pointer',
          width: '100%',
          display: 'flex',
          alignItems: 'baseline',
          gap: 10,
          justifyContent: 'space-between',
        }}
      >
        <span style={{ display: 'flex', alignItems: 'baseline', gap: 8, minWidth: 0 }}>
          <span aria-hidden="true" style={{ color: 'var(--fg-3, #9a9aa2)' }}>
            {expanded ? '▾' : '▸'}
          </span>
          <strong
            style={{
              fontSize: 13,
              fontWeight: 600,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {goal.title || goal.id}
          </strong>
        </span>
        <span style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
          <span style={pillStyle(goal.status)}>{goal.status}</span>
          <span style={{ fontSize: 11, color: 'var(--fg-3, #9a9aa2)' }}>
            {relTime(goal.updated_at ?? goal.created_at)}
          </span>
        </span>
      </button>
      {expanded ? (
        <div style={{ marginTop: 8, paddingLeft: 18, fontSize: 12, color: 'var(--fg-2, #d0d0d6)' }}>
          {goal.description ? (
            <p style={{ margin: '0 0 6px' }}>{goal.description}</p>
          ) : null}
          {goal.pc_reconciliation_state ? (
            <p style={{ margin: '0 0 6px', fontSize: 11.5, color: 'var(--fg-3, #9a9aa2)' }}>
              reconciliation: <code>{goal.pc_reconciliation_state}</code>
              {typeof goal.pc_freshness === 'number'
                ? ` · freshness ${goal.pc_freshness.toFixed(2)}`
                : null}
              {goal.pc_unfinished_item_state && goal.pc_unfinished_item_state !== 'none'
                ? ` · ${goal.pc_unfinished_item_state}`
                : null}
            </p>
          ) : null}
          <p style={{ margin: '6px 0 4px', fontSize: 11, color: 'var(--fg-3, #9a9aa2)' }}>
            Linked artefacts ({artefacts.length})
          </p>
          {artefacts.length === 0 ? (
            <p style={{ margin: 0, fontSize: 11.5 }}>None linked yet.</p>
          ) : (
            <ul style={{ margin: 0, paddingLeft: 16 }}>
              {artefacts.map((artefact) => {
                const detail = detailByArtefact[artefact.id]
                return (
                  <li key={artefact.id} style={{ marginBottom: 6 }}>
                    <button
                      type="button"
                      onClick={() => onLoadArtefact(artefact.id)}
                      style={{
                        all: 'unset',
                        cursor: 'pointer',
                        color: 'var(--accent, #5eb2ff)',
                        fontSize: 12,
                      }}
                    >
                      {artefact.title || artefact.source_uri || artefact.id}
                    </button>
                    <span
                      style={{
                        marginLeft: 6,
                        fontSize: 11,
                        color: 'var(--fg-3, #9a9aa2)',
                      }}
                    >
                      {artefact.connector ?? artefact.kind ?? 'artefact'}
                      {artefact.status ? ` · ${artefact.status}` : null}
                    </span>
                    {detail && detail.recent_outcomes.length > 0 ? (
                      <p
                        style={{
                          margin: '2px 0 0',
                          fontSize: 11,
                          color: 'var(--fg-3, #9a9aa2)',
                        }}
                      >
                        last reconciled {relTime(detail.recent_outcomes[0].pass_at)}
                      </p>
                    ) : null}
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      ) : null}
    </li>
  )
}

export interface GoalsPanelProps {
  /** Override polling interval for tests. */
  refreshIntervalMs?: number
}

export function GoalsPanel({ refreshIntervalMs = REFRESH_INTERVAL_MS }: GoalsPanelProps = {}) {
  const [goals, setGoals] = useState<Goal[]>([])
  const [artefactsByGoal, setArtefactsByGoal] = useState<Record<string, Artefact[]>>({})
  const [detailByArtefact, setDetailByArtefact] = useState<Record<string, ArtefactDetail | null>>({})
  const [expanded, setExpanded] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [goalsResp, artefactsResp] = await Promise.all([
        listGoals({ limit: 50 }),
        listArtefacts({ limit: 200 }),
      ])
      setGoals(goalsResp.goals)
      const grouped: Record<string, Artefact[]> = {}
      for (const artefact of artefactsResp.artefacts) {
        for (const goalId of artefact.linked_goals ?? []) {
          if (!grouped[goalId]) grouped[goalId] = []
          grouped[goalId].push(artefact)
        }
      }
      setArtefactsByGoal(grouped)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoaded(true)
    }
  }, [])

  useEffect(() => {
    void refresh()
    const handle = window.setInterval(() => void refresh(), refreshIntervalMs)
    return () => window.clearInterval(handle)
  }, [refresh, refreshIntervalMs])

  const handleToggle = useCallback(
    (goalId: string) => {
      setExpanded((current) => (current === goalId ? null : goalId))
      const linked = artefactsByGoal[goalId] ?? []
      for (const artefact of linked) {
        if (detailByArtefact[artefact.id] === undefined) {
          void fetchArtefact(artefact.id).then((detail) =>
            setDetailByArtefact((prev) => ({ ...prev, [artefact.id]: detail })),
          )
        }
      }
    },
    [artefactsByGoal, detailByArtefact],
  )

  const handleLoadArtefact = useCallback((id: string) => {
    void fetchArtefact(id).then((detail) =>
      setDetailByArtefact((prev) => ({ ...prev, [id]: detail })),
    )
  }, [])

  const summary = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const goal of goals) counts[goal.status] = (counts[goal.status] ?? 0) + 1
    return counts
  }, [goals])

  return (
    <section
      aria-labelledby="goals-panel-heading"
      role="region"
      style={cardStyle()}
      data-testid="goals-panel"
    >
      <header style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
        <h2
          id="goals-panel-heading"
          style={{ margin: 0, fontSize: 14, fontWeight: 600 }}
        >
          Goals
        </h2>
        <span style={{ fontSize: 11, color: 'var(--fg-3, #9a9aa2)' }}>
          {Object.entries(summary)
            .map(([status, count]) => `${count} ${status}`)
            .join(' · ') || (loaded ? 'no goals declared' : 'loading…')}
        </span>
      </header>
      {error ? (
        <p
          role="alert"
          style={{ margin: 0, fontSize: 12, color: 'var(--amber, #e6b656)' }}
        >
          {error}
        </p>
      ) : null}
      {loaded && goals.length === 0 ? (
        <p style={{ margin: 0, fontSize: 12, color: 'var(--fg-2, #d0d0d6)' }}>
          No workspace goals declared. Use the MCP tool{' '}
          <code>vaner.goals.declare</code> to create one.
        </p>
      ) : (
        <ul style={{ margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {goals.map((goal) => (
            <GoalRow
              key={goal.id}
              goal={goal}
              expanded={expanded === goal.id}
              onToggle={() => handleToggle(goal.id)}
              artefactsByGoal={artefactsByGoal}
              detailByArtefact={detailByArtefact}
              onLoadArtefact={handleLoadArtefact}
            />
          ))}
        </ul>
      )}
    </section>
  )
}
