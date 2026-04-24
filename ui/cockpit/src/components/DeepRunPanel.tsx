// SPDX-License-Identifier: Apache-2.0
// 0.8.3 WS4 — Cockpit Deep-Run panel.  One self-contained React
// component that renders both the active-session pill and the
// start-controls form.  Drop into any layout slot in App.tsx; once
// the cockpit's missing api/client.ts lands the existing components
// can compose this without further wiring.
//
// Honest 4-counter discipline (spec §9.2 / §14.1): the active-session
// pill always renders kept / discarded / rolled-back / failed as four
// separate numbers, never collapsed into a single "matured" total.

import { useCallback, useEffect, useState } from 'react'

import {
  getDeepRunStatus,
  listDeepRunSessions,
  startDeepRun,
  stopDeepRun,
} from '../api/deepRun'
import type {
  DeepRunHorizonBias,
  DeepRunFocus,
  DeepRunLocality,
  DeepRunPreset,
  DeepRunSession,
} from '../types/deepRun'

const PRESET_OPTIONS: DeepRunPreset[] = ['conservative', 'balanced', 'aggressive']
const FOCUS_OPTIONS: DeepRunFocus[] = ['active_goals', 'current_workspace', 'all_recent']
const HORIZON_OPTIONS: DeepRunHorizonBias[] = [
  'likely_next',
  'long_horizon',
  'finish_partials',
  'balanced',
]
const LOCALITY_OPTIONS: DeepRunLocality[] = [
  'local_only',
  'local_preferred',
  'allow_cloud',
]

type DeepRunFormState = {
  untilHours: number
  preset: DeepRunPreset
  focus: DeepRunFocus
  horizon: DeepRunHorizonBias
  locality: DeepRunLocality
  costCapUsd: number
}

const DEFAULT_FORM: DeepRunFormState = {
  untilHours: 8,
  preset: 'balanced',
  focus: 'active_goals',
  horizon: 'balanced',
  locality: 'local_preferred',
  costCapUsd: 0,
}

function _formatTime(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  })
}

function _formatRemaining(endsAt: number): string {
  const remainingSec = Math.max(0, endsAt - Date.now() / 1000)
  if (remainingSec <= 0) return 'expired'
  const h = Math.floor(remainingSec / 3600)
  const m = Math.floor((remainingSec % 3600) / 60)
  if (h > 0) return `${h}h ${m}m left`
  return `${m}m left`
}

interface DeepRunPillProps {
  session: DeepRunSession
  onStop: () => void | Promise<void>
}

export function DeepRunPill({ session, onStop }: DeepRunPillProps): JSX.Element {
  const totalAttempts =
    session.matured_kept +
    session.matured_discarded +
    session.matured_rolled_back +
    session.matured_failed
  return (
    <div
      className={`deep-run-pill deep-run-pill--${session.preset}`}
      data-testid="deep-run-pill"
    >
      <span className="deep-run-pill__badge">
        {session.status === 'paused' ? '⏸︎ paused' : '🌙 deep-run'}
      </span>
      <span className="deep-run-pill__preset">{session.preset}</span>
      <span className="deep-run-pill__remaining">
        {_formatRemaining(session.ends_at)} (until {_formatTime(session.ends_at)})
      </span>
      <span className="deep-run-pill__cycles">cycle {session.cycles_run}</span>
      {/* Honest 4-counter discipline — surface every outcome separately. */}
      <span className="deep-run-pill__matured" title="kept / discarded / rolled-back / failed">
        matured: {session.matured_kept}/{session.matured_discarded}/
        {session.matured_rolled_back}/{session.matured_failed} of {totalAttempts}
      </span>
      {session.cost_cap_usd > 0 ? (
        <span className="deep-run-pill__spend">
          ${session.spend_usd.toFixed(2)} / ${session.cost_cap_usd.toFixed(2)}
        </span>
      ) : (
        <span className="deep-run-pill__spend">local-only (no remote spend)</span>
      )}
      {session.pause_reasons.length > 0 ? (
        <span className="deep-run-pill__pause-reasons">
          paused: {session.pause_reasons.join(', ')}
        </span>
      ) : null}
      <button
        type="button"
        onClick={() => {
          void onStop()
        }}
        className="deep-run-pill__stop"
        data-testid="deep-run-stop"
      >
        stop
      </button>
    </div>
  )
}

interface DeepRunStartCardProps {
  onStarted: (session: DeepRunSession) => void
}

export function DeepRunStartCard({ onStarted }: DeepRunStartCardProps): JSX.Element {
  const [form, setForm] = useState<DeepRunFormState>(DEFAULT_FORM)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const submit = useCallback(async () => {
    setSubmitting(true)
    setError(null)
    try {
      const session = await startDeepRun({
        ends_at: Date.now() / 1000 + form.untilHours * 3600,
        preset: form.preset,
        focus: form.focus,
        horizon_bias: form.horizon,
        locality: form.locality,
        cost_cap_usd: form.costCapUsd,
        metadata: { caller: 'cockpit' },
      })
      onStarted(session)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }, [form, onStarted])

  return (
    <div className="deep-run-start-card" data-testid="deep-run-start-card">
      <h3>Start Deep-Run</h3>
      <label>
        Until (hours)
        <input
          type="number"
          min={0.25}
          step={0.25}
          value={form.untilHours}
          onChange={(e) => setForm({ ...form, untilHours: Number(e.target.value) })}
        />
      </label>
      <label>
        Preset
        <select
          value={form.preset}
          onChange={(e) => setForm({ ...form, preset: e.target.value as DeepRunPreset })}
        >
          {PRESET_OPTIONS.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </label>
      <label>
        Focus
        <select
          value={form.focus}
          onChange={(e) => setForm({ ...form, focus: e.target.value as DeepRunFocus })}
        >
          {FOCUS_OPTIONS.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
      </label>
      <label>
        Horizon bias
        <select
          value={form.horizon}
          onChange={(e) =>
            setForm({ ...form, horizon: e.target.value as DeepRunHorizonBias })
          }
        >
          {HORIZON_OPTIONS.map((h) => (
            <option key={h} value={h}>
              {h}
            </option>
          ))}
        </select>
      </label>
      <label>
        Locality
        <select
          value={form.locality}
          onChange={(e) =>
            setForm({ ...form, locality: e.target.value as DeepRunLocality })
          }
        >
          {LOCALITY_OPTIONS.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
      </label>
      <label>
        Cost cap (USD; 0 ⇒ no remote spend)
        <input
          type="number"
          min={0}
          step={0.5}
          value={form.costCapUsd}
          onChange={(e) => setForm({ ...form, costCapUsd: Number(e.target.value) })}
        />
      </label>
      {error ? (
        <div className="deep-run-start-card__error" role="alert">
          {error}
        </div>
      ) : null}
      <button
        type="button"
        disabled={submitting}
        onClick={() => {
          void submit()
        }}
      >
        {submitting ? 'Starting…' : 'Start Deep-Run'}
      </button>
    </div>
  )
}

export function DeepRunPanel(): JSX.Element {
  const [session, setSession] = useState<DeepRunSession | null>(null)
  const [history, setHistory] = useState<DeepRunSession[]>([])
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      const [statusResp, listResp] = await Promise.all([
        getDeepRunStatus(),
        listDeepRunSessions(10),
      ])
      setSession(statusResp.session)
      setHistory(listResp.sessions)
    } catch {
      // Daemon offline / network error — leave state as-is and let the
      // poll retry on the next interval.
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
    const interval = window.setInterval(() => {
      void refresh()
    }, 5000)
    return () => window.clearInterval(interval)
  }, [refresh])

  const onStop = useCallback(async () => {
    await stopDeepRun()
    await refresh()
  }, [refresh])

  const onStarted = useCallback(
    (started: DeepRunSession) => {
      setSession(started)
      void refresh()
    },
    [refresh],
  )

  return (
    <div className="deep-run-panel">
      {loading ? (
        <div className="deep-run-panel__loading">Loading Deep-Run state…</div>
      ) : session ? (
        <DeepRunPill session={session} onStop={onStop} />
      ) : (
        <DeepRunStartCard onStarted={onStarted} />
      )}
      {history.length > 0 ? (
        <details className="deep-run-panel__history">
          <summary>History ({history.length})</summary>
          <table>
            <thead>
              <tr>
                <th>id</th>
                <th>status</th>
                <th>preset</th>
                <th>started</th>
                <th>cycles</th>
                <th>matured (k/d/r/f)</th>
                <th>spend</th>
              </tr>
            </thead>
            <tbody>
              {history.map((s) => (
                <tr key={s.id}>
                  <td>
                    <code>{s.id.slice(0, 8)}</code>
                  </td>
                  <td>{s.status}</td>
                  <td>{s.preset}</td>
                  <td>{_formatTime(s.started_at)}</td>
                  <td>{s.cycles_run}</td>
                  <td>
                    {s.matured_kept}/{s.matured_discarded}/
                    {s.matured_rolled_back}/{s.matured_failed}
                  </td>
                  <td>${s.spend_usd.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      ) : null}
    </div>
  )
}
