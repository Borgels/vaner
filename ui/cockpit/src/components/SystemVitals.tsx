import { useEffect, useState } from 'react'

import type { CycleState, ModelState } from '../api/usePipelineEvents'

export interface SystemVitalsProps {
  live: boolean
  mode: string
  cycle: CycleState
  model: ModelState
  scenarioCount: number
  pendingLlm: number
}

function formatDuration(ms: number | null | undefined): string {
  if (!ms || ms < 0) {
    return '—'
  }
  if (ms < 1000) {
    return `${ms.toFixed(0)}ms`
  }
  if (ms < 60_000) {
    return `${(ms / 1000).toFixed(2)}s`
  }
  const minutes = Math.floor(ms / 60_000)
  const seconds = Math.floor((ms % 60_000) / 1000)
  return `${minutes}m${seconds.toString().padStart(2, '0')}`
}

function ema(values: number[]): number {
  if (!values.length) {
    return 0
  }
  const alpha = 2 / (values.length + 1)
  return values.reduce((acc, value, index) => (index === 0 ? value : alpha * value + (1 - alpha) * acc), 0)
}

/**
 * Compact live-vitals panel surfacing daemon / model / cycle state.
 *
 * Rendered in the left rail above the skills panel so the user can see in one
 * glance whether the cockpit SSE is tailing, whether a daemon cycle is in
 * flight, whether the model is currently busy, and the recent LLM latency.
 * All values are derived from the unified event bus — nothing is polled.
 */
export function SystemVitals({ live, mode, cycle, model, scenarioCount, pendingLlm }: SystemVitalsProps) {
  const [now, setNow] = useState(() => Date.now() / 1000)

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 500)
    return () => window.clearInterval(timer)
  }, [])

  const avgLatency = ema(model.recentLatencies)
  const running = cycle.current !== null
  const elapsedCycleMs = running ? Math.max(0, (now - cycle.current!.startedAt) * 1000) : null

  return (
    <div
      style={{
        padding: '14px 16px 12px',
        borderBottom: '1px solid var(--line-hair)',
        background: 'var(--bg-0)',
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
      }}
      aria-label="System vitals"
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div className="mono" style={{ fontSize: 9.5, letterSpacing: 1.2, color: 'var(--fg-4)' }}>
          SYSTEM VITALS
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span
            aria-hidden
            style={{
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: live ? 'var(--ok)' : 'var(--err)',
              boxShadow: live ? '0 0 8px var(--ok)' : 'none',
              animation: live ? 'dc-pulse 1.4s infinite' : 'none',
            }}
          />
          <span className="mono" style={{ fontSize: 10, color: 'var(--fg-3)' }}>
            {live ? 'live' : 'offline'}
          </span>
        </div>
      </div>

      <VitalRow label="mode" value={mode} />
      <VitalRow
        label="cycle"
        emphasize={running}
        value={
          running ? (
            <span style={{ color: 'var(--accent)' }}>
              running · {formatDuration(elapsedCycleMs)}
            </span>
          ) : cycle.lastFinished ? (
            <span>
              idle · last {formatDuration(cycle.lastFinished.durationMs)}
            </span>
          ) : (
            'waiting'
          )
        }
      />
      <VitalRow
        label="model"
        emphasize={pendingLlm > 0}
        value={
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            {pendingLlm > 0 ? (
              <span
                aria-hidden
                style={{
                  width: 9,
                  height: 9,
                  borderRadius: '50%',
                  border: '1.5px solid var(--amber)',
                  borderTopColor: 'transparent',
                  animation: 'dc-spin 0.8s linear infinite',
                  display: 'inline-block',
                }}
              />
            ) : null}
            <span style={{ color: pendingLlm > 0 ? 'var(--amber)' : 'var(--fg-2)' }}>
              {pendingLlm > 0 ? `busy · ${pendingLlm}` : model.lastModel ? 'idle' : 'standby'}
            </span>
          </span>
        }
      />
      <VitalRow label="last latency" value={formatDuration(model.lastLatencyMs)} />
      <VitalRow label="ema latency" value={formatDuration(avgLatency || null)} />
      <VitalRow label="model id" value={model.lastModel ?? '—'} mono title={model.lastModel ?? undefined} />
      <VitalRow label="cycles" value={`${cycle.totalCycles}`} />
      <VitalRow label="artefacts" value={`${cycle.artefactsWritten}`} />
      <VitalRow label="scenarios" value={`${scenarioCount}`} />
      {model.totalErrors > 0 ? (
        <VitalRow
          label="llm errors"
          value={<span style={{ color: 'var(--err)' }}>{model.totalErrors}</span>}
        />
      ) : null}
    </div>
  )
}

interface VitalRowProps {
  label: string
  value: React.ReactNode
  emphasize?: boolean
  mono?: boolean
  title?: string
}

function VitalRow({ label, value, emphasize, mono, title }: VitalRowProps) {
  return (
    <div
      title={title}
      style={{
        display: 'flex',
        alignItems: 'baseline',
        justifyContent: 'space-between',
        gap: 8,
        fontFamily: 'var(--font-mono)',
        fontSize: 10.5,
        color: emphasize ? 'var(--fg-1)' : 'var(--fg-2)',
        letterSpacing: 0.2,
      }}
    >
      <span style={{ color: 'var(--fg-4)' }}>{label}</span>
      <span
        style={{
          maxWidth: 160,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          textAlign: 'right',
          fontVariantNumeric: mono ? 'tabular-nums' : undefined,
        }}
      >
        {value}
      </span>
    </div>
  )
}
