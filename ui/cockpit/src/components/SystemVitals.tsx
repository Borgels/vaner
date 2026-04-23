import { useEffect, useState } from 'react'

import type { CycleState, ModelState } from '../api/usePipelineEvents'
import type { BucketBudgets } from '../types'

export interface SystemVitalsProps {
  live: boolean
  mode: string
  cycle: CycleState
  model: ModelState
  scenarioCount: number
  pendingLlm: number
  predictionMetrics?: {
    next_prompt_top1_rate?: number
    next_prompt_top3_rate?: number
    next_prompt_logloss?: number
    next_prompt_brier?: number
    draft_usefulness_rate?: number
    budget_utilization?: number
    predictive_lead_seconds_avg?: number
    confidence_conditioned_utility?: number
    bucket_budgets?: BucketBudgets
  } | null
  predictionCalibration?: Array<{
    bucket: number
    confidence_mid: number
    count: number
    accuracy: number
  }> | null
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
export function SystemVitals({
  live,
  mode,
  cycle,
  model,
  scenarioCount,
  pendingLlm,
  predictionMetrics,
  predictionCalibration,
}: SystemVitalsProps) {
  const calibrationEce =
    predictionCalibration && predictionCalibration.length
      ? predictionCalibration.reduce(
          (acc, row) => acc + (Math.abs(row.accuracy - row.confidence_mid) * row.count),
          0,
        ) / Math.max(1, predictionCalibration.reduce((acc, row) => acc + row.count, 0))
      : null
  const reliabilityMini =
    predictionCalibration && predictionCalibration.length
      ? predictionCalibration
          .map((row) => {
            if (row.count <= 0) {
              return '·'
            }
            const gap = Math.abs(row.accuracy - row.confidence_mid)
            if (gap < 0.05) return '█'
            if (gap < 0.1) return '▓'
            if (gap < 0.2) return '▒'
            return '░'
          })
          .join('')
      : null
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
      <VitalRow
        label="pred top1"
        value={predictionMetrics?.next_prompt_top1_rate != null ? `${(predictionMetrics.next_prompt_top1_rate * 100).toFixed(1)}%` : '—'}
      />
      <VitalRow
        label="pred top3"
        value={predictionMetrics?.next_prompt_top3_rate != null ? `${(predictionMetrics.next_prompt_top3_rate * 100).toFixed(1)}%` : '—'}
      />
      <VitalRow
        label="calib brier"
        value={predictionMetrics?.next_prompt_brier != null ? predictionMetrics.next_prompt_brier.toFixed(3) : '—'}
      />
      <VitalRow label="calib ece" value={calibrationEce != null ? calibrationEce.toFixed(3) : '—'} />
      <VitalRow label="reliability" value={reliabilityMini ?? '—'} mono />
      {predictionCalibration && predictionCalibration.length ? (
        <ReliabilityChart rows={predictionCalibration} />
      ) : null}
      {predictionMetrics?.bucket_budgets ? (
        <BucketBudgetPanel bucketBudgets={predictionMetrics.bucket_budgets} />
      ) : null}
      <VitalRow
        label="pred logloss"
        value={predictionMetrics?.next_prompt_logloss != null ? predictionMetrics.next_prompt_logloss.toFixed(3) : '—'}
      />
      <VitalRow
        label="draft useful"
        value={predictionMetrics?.draft_usefulness_rate != null ? `${(predictionMetrics.draft_usefulness_rate * 100).toFixed(1)}%` : '—'}
      />
      <VitalRow
        label="budget util"
        value={predictionMetrics?.budget_utilization != null ? `${(predictionMetrics.budget_utilization * 100).toFixed(1)}%` : '—'}
      />
      <VitalRow
        label="lead time"
        value={
          predictionMetrics?.predictive_lead_seconds_avg != null
            ? `${predictionMetrics.predictive_lead_seconds_avg.toFixed(1)}s`
            : '—'
        }
      />
      <VitalRow
        label="conf utility"
        value={
          predictionMetrics?.confidence_conditioned_utility != null
            ? predictionMetrics.confidence_conditioned_utility.toFixed(3)
            : '—'
        }
      />
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

interface ReliabilityChartProps {
  rows: Array<{ bucket: number; confidence_mid: number; count: number; accuracy: number }>
}

/**
 * Compact SVG reliability diagram: each bucket is a vertical bar whose height
 * is observed accuracy, width is proportional to bucket count. A diagonal line
 * marks perfect calibration; gap-to-diagonal shows miscalibration direction.
 */
function ReliabilityChart({ rows }: ReliabilityChartProps) {
  const width = 160
  const height = 60
  const padX = 4
  const padY = 6
  const plotW = width - padX * 2
  const plotH = height - padY * 2
  const maxCount = Math.max(1, ...rows.map((r) => r.count))
  const barWidth = plotW / Math.max(1, rows.length)

  return (
    <div
      aria-label="Calibration reliability"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
        paddingTop: 4,
      }}
    >
      <div className="mono" style={{ fontSize: 9, color: 'var(--fg-4)', letterSpacing: 0.8 }}>
        RELIABILITY
      </div>
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Reliability diagram"
        style={{ display: 'block' }}
      >
        {/* Frame */}
        <rect
          x={padX}
          y={padY}
          width={plotW}
          height={plotH}
          fill="none"
          stroke="var(--line-hair)"
          strokeWidth={0.5}
        />
        {/* Diagonal reference line — perfect calibration */}
        <line
          x1={padX}
          y1={padY + plotH}
          x2={padX + plotW}
          y2={padY}
          stroke="var(--fg-4)"
          strokeWidth={0.5}
          strokeDasharray="2,2"
          opacity={0.6}
        />
        {rows.map((row, idx) => {
          if (row.count <= 0) return null
          const x = padX + idx * barWidth + 1
          const barH = row.accuracy * plotH
          const y = padY + plotH - barH
          const intensity = Math.max(0.18, Math.min(1, row.count / maxCount))
          const gap = Math.abs(row.accuracy - row.confidence_mid)
          // Under-confident: bar above diagonal; over-confident: below.
          // Color accent scales with miscalibration size.
          const fill =
            gap < 0.05
              ? 'var(--ok)'
              : gap < 0.15
                ? 'var(--accent)'
                : 'var(--amber)'
          return (
            <rect
              key={idx}
              x={x}
              y={y}
              width={Math.max(1, barWidth - 2)}
              height={barH}
              fill={fill}
              opacity={intensity}
            >
              <title>
                {`bucket ${(row.confidence_mid * 100).toFixed(0)}%: ` +
                  `acc=${(row.accuracy * 100).toFixed(0)}% n=${row.count}`}
              </title>
            </rect>
          )
        })}
      </svg>
    </div>
  )
}

interface BucketBudgetPanelProps {
  bucketBudgets: BucketBudgets
}

/**
 * Per-bucket allocated / used / util for the portfolio allocator.
 * Gives operators direct visibility into how the cycle budget split actually
 * played out (vs. just the aggregate ``budget util`` scalar).
 */
function BucketBudgetPanel({ bucketBudgets }: BucketBudgetPanelProps) {
  const buckets: Array<{ key: keyof BucketBudgets; label: string }> = [
    { key: 'exploit', label: 'exploit' },
    { key: 'hedge', label: 'hedge' },
    { key: 'invest', label: 'invest' },
    { key: 'no_regret', label: 'no regret' },
  ]

  return (
    <div
      aria-label="Per-bucket budget"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
        paddingTop: 4,
      }}
    >
      <div className="mono" style={{ fontSize: 9, color: 'var(--fg-4)', letterSpacing: 0.8 }}>
        BUDGET BUCKETS
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr auto auto auto',
          columnGap: 8,
          rowGap: 2,
          fontFamily: 'var(--font-mono)',
          fontSize: 10,
          color: 'var(--fg-2)',
        }}
      >
        <span style={{ color: 'var(--fg-4)', fontSize: 9 }}>bucket</span>
        <span style={{ color: 'var(--fg-4)', fontSize: 9, textAlign: 'right' }}>alloc</span>
        <span style={{ color: 'var(--fg-4)', fontSize: 9, textAlign: 'right' }}>used</span>
        <span style={{ color: 'var(--fg-4)', fontSize: 9, textAlign: 'right' }}>util</span>
        {buckets.map(({ key, label }) => {
          const row = bucketBudgets[key]
          const alloc = row?.allocated_ms ?? 0
          const used = row?.used_ms ?? 0
          const util = alloc > 0 ? used / alloc : 0
          const utilColor =
            util > 1.1 ? 'var(--err)' : util > 0.9 ? 'var(--ok)' : util > 0.5 ? 'var(--accent)' : 'var(--fg-3)'
          return (
            <>
              <span key={`${key}-label`} style={{ color: 'var(--fg-3)' }}>
                {label}
              </span>
              <span
                key={`${key}-alloc`}
                style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}
              >
                {formatBudget(alloc)}
              </span>
              <span
                key={`${key}-used`}
                style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}
              >
                {formatBudget(used)}
              </span>
              <span
                key={`${key}-util`}
                style={{
                  textAlign: 'right',
                  fontVariantNumeric: 'tabular-nums',
                  color: utilColor,
                }}
              >
                {alloc > 0 ? `${(util * 100).toFixed(0)}%` : '—'}
              </span>
            </>
          )
        })}
      </div>
    </div>
  )
}

function formatBudget(ms: number): string {
  if (!ms || ms <= 0) return '—'
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}
