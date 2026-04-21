import { useState } from 'react'

import { KIND_COLOR } from '../lib/constants'
import type { ScenarioApiPayload, UIEvidence, UIScenario } from '../types'

export type ScoreComponent = NonNullable<ScenarioApiPayload['score_components']>[number]

function FieldBlock({
  title,
  meta,
  children,
  right,
}: {
  title: string
  meta?: string
  children: React.ReactNode
  right?: React.ReactNode
}) {
  const [open, setOpen] = useState(true)
  return (
    <div style={{ borderTop: '1px solid var(--line-hair)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 18px 10px' }}>
        <button
          onClick={() => setOpen((value) => !value)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            background: 'transparent',
            border: 'none',
            color: 'var(--fg-2)',
            cursor: 'pointer',
            fontFamily: 'var(--font-display)',
            fontSize: 10.5,
            fontWeight: 500,
            letterSpacing: 1.2,
            textTransform: 'uppercase',
            padding: 0,
          }}
        >
          <svg width="8" height="8" viewBox="0 0 8 8" style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform .15s' }}>
            <path d="M2 1 L6 4 L2 7" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          {title}
          {meta ? (
            <span className="mono" style={{ fontSize: 9.5, letterSpacing: 0.4, color: 'var(--fg-4)', marginLeft: 6, textTransform: 'none', fontWeight: 400 }}>
              {meta}
            </span>
          ) : null}
        </button>
        {right}
      </div>
      {open ? <div style={{ padding: '0 18px 16px' }}>{children}</div> : null}
    </div>
  )
}

function Pill({
  children,
  color = 'var(--fg-3)',
  bg,
  onClick,
}: {
  children: React.ReactNode
  color?: string
  bg?: string
  onClick?: () => void
}) {
  return (
    <span
      onClick={onClick}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 5,
        padding: '3px 8px',
        borderRadius: 'var(--r-1)',
        background: bg ?? 'var(--bg-inset)',
        border: '1px solid var(--line-hair)',
        fontFamily: 'var(--font-mono)',
        fontSize: 10.5,
        color,
        letterSpacing: 0.2,
        cursor: onClick ? 'pointer' : 'default',
      }}
    >
      {children}
    </span>
  )
}

function Verdict({ state, reason, packageId }: { state: UIScenario['decisionState']; reason: string; packageId?: string | null }) {
  const map: Record<UIScenario['decisionState'], { label: string; color: string; symbol: string }> = {
    chosen: { label: 'CHOSEN', color: 'var(--ok)', symbol: '✓' },
    partial: { label: 'PARTIAL', color: 'var(--amber)', symbol: '◐' },
    rejected: { label: 'REJECTED', color: 'var(--fg-4)', symbol: '✕' },
    pending: { label: 'PENDING', color: 'var(--accent)', symbol: '◌' },
    active: { label: 'EXPANDING', color: 'var(--accent)', symbol: '●' },
    idle: { label: 'IDLE', color: 'var(--fg-4)', symbol: '○' },
  }
  const verdict = map[state]

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 12,
        padding: '14px 16px',
        borderRadius: 'var(--r-2)',
        background: `color-mix(in oklch, ${verdict.color} 14%, var(--bg-2))`,
        border: `1px solid color-mix(in oklch, ${verdict.color} 40%, var(--line-1))`,
      }}
    >
      <div
        style={{
          width: 28,
          height: 28,
          borderRadius: '50%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
          background: verdict.color,
          color: 'var(--bg-0)',
          fontWeight: 700,
          fontSize: 14,
        }}
      >
        {verdict.symbol}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="mono" style={{ fontSize: 10, letterSpacing: 1.2, color: verdict.color, fontWeight: 600 }}>
          {verdict.label}
          {packageId ? <span style={{ color: 'var(--fg-3)', marginLeft: 8 }}>→ {packageId}</span> : null}
        </div>
        <div style={{ fontSize: 13, color: 'var(--fg-1)', marginTop: 4, lineHeight: 1.5 }}>{reason}</div>
      </div>
    </div>
  )
}

function ScoreBreakdown({ scenario, components }: { scenario: UIScenario; components: ScoreComponent[] }) {
  const base = scenario.score

  if (!components.length) {
    return (
      <div style={{ fontSize: 11.5, color: 'var(--fg-4)', padding: '8px 0' }}>
        No score components reported yet. The daemon only publishes a breakdown after it runs the next
        scoring pass.
      </div>
    )
  }

  const max = Math.max(...components.map((part) => Math.abs(Number(part.value) || 0)))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
      {components.map((part, index) => {
        const value = Number(part.value) || 0
        return (
          <div key={index} style={{ display: 'flex', alignItems: 'center', gap: 10, fontFamily: 'var(--font-mono)', fontSize: 11 }}>
            <span style={{ flex: 1, color: 'var(--fg-2)' }} title={part.description ?? undefined}>
              {part.label}
            </span>
            <span style={{ width: 120, height: 4, background: 'var(--bg-inset)', borderRadius: 2, position: 'relative' }}>
              <span
                style={{
                  position: 'absolute',
                  height: '100%',
                  borderRadius: 2,
                  left: value >= 0 ? '50%' : `${50 + (value / max) * 50}%`,
                  width: `${Math.min(50, Math.abs((value / max) * 50))}%`,
                  background: value >= 0 ? 'var(--accent)' : 'var(--err)',
                }}
              />
              <span style={{ position: 'absolute', left: '50%', top: -1, bottom: -1, width: 1, background: 'var(--line-2)' }} />
            </span>
            <span style={{ width: 52, textAlign: 'right', color: value >= 0 ? 'var(--accent)' : 'var(--err)', fontVariantNumeric: 'tabular-nums' }}>
              {value >= 0 ? '+' : ''}
              {value.toFixed(3)}
            </span>
          </div>
        )
      })}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, paddingTop: 8, borderTop: '1px solid var(--line-hair)', marginTop: 4 }}>
        <span style={{ flex: 1, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-1)' }}>final</span>
        <span style={{ width: 120, height: 4, background: 'var(--bg-inset)', borderRadius: 2, overflow: 'hidden' }}>
          <span style={{ display: 'block', width: `${base * 100}%`, height: '100%', background: KIND_COLOR[scenario.kind] }} />
        </span>
        <span className="mono" style={{ width: 52, textAlign: 'right', color: KIND_COLOR[scenario.kind], fontWeight: 500 }}>
          {base.toFixed(3)}
        </span>
      </div>
    </div>
  )
}

function highlightLine(line: string) {
  const keywords = /\b(def|class|return|for|in|if|not|async|await|yield|import|from|with|as|True|False|None)\b/g
  const pieces: React.ReactNode[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null
  keywords.lastIndex = 0
  while ((match = keywords.exec(line))) {
    pieces.push(line.slice(lastIndex, match.index))
    pieces.push(
      <span key={`${match.index}-${match[0]}`} style={{ color: 'var(--accent)' }}>
        {match[0]}
      </span>,
    )
    lastIndex = match.index + match[0].length
  }
  pieces.push(line.slice(lastIndex))
  return pieces
}

function CodeEvidence({ file, lines, startLine, note }: UIEvidence) {
  const body = note || `Preview unavailable for ${file}`
  const rows = body.split('\n')
  const firstLine = startLine ?? null

  return (
    <div style={{ background: 'var(--bg-inset)', border: '1px solid var(--line-hair)', borderRadius: 'var(--r-2)', overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', padding: '8px 12px', borderBottom: '1px solid var(--line-hair)' }}>
        <span className="mono" style={{ fontSize: 11, color: 'var(--fg-1)' }}>
          {file}
        </span>
        {lines ? (
          <span className="mono" style={{ fontSize: 10, color: 'var(--amber)' }}>
            L{lines}
          </span>
        ) : null}
      </div>
      <pre className="mono" style={{ margin: 0, padding: '8px 0', fontSize: 11, lineHeight: 1.55, color: 'var(--fg-2)', maxHeight: 180, overflow: 'auto' }}>
        {rows.map((row, index) => (
          <div key={index} style={{ display: 'flex', padding: '0 12px' }}>
            {firstLine != null ? (
              <span style={{ width: 36, color: 'var(--fg-4)', userSelect: 'none', textAlign: 'right', marginRight: 10 }}>
                {firstLine + index}
              </span>
            ) : null}
            <span style={{ whiteSpace: 'pre', flex: 1 }}>{highlightLine(row)}</span>
          </div>
        ))}
      </pre>
      <div style={{ padding: '8px 12px', borderTop: '1px solid var(--line-hair)', fontSize: 11.5, color: 'var(--fg-3)', lineHeight: 1.5 }}>{note}</div>
    </div>
  )
}

function PreparedContext({ json }: { json: string }) {
  let object: unknown
  try {
    object = JSON.parse(json)
  } catch {
    object = null
  }

  if (!object) {
    return <pre className="mono" style={{ background: 'var(--bg-inset)', border: '1px solid var(--line-hair)', borderRadius: 'var(--r-2)', padding: 12, fontSize: 11, color: 'var(--fg-2)', margin: 0, maxHeight: 160, overflow: 'auto' }}>{json}</pre>
  }

  const render = (value: unknown, depth = 0): React.ReactNode => {
    if (Array.isArray(value)) {
      return (
        <span>
          <span style={{ color: 'var(--fg-3)' }}>[</span>
          {value.map((item, index) => (
            <span key={index}>
              {index > 0 ? <span style={{ color: 'var(--fg-4)' }}>, </span> : null}
              {render(item, depth + 1)}
            </span>
          ))}
          <span style={{ color: 'var(--fg-3)' }}>]</span>
        </span>
      )
    }

    if (value && typeof value === 'object') {
      return (
        <div style={{ paddingLeft: depth ? 14 : 0 }}>
          {Object.entries(value).map(([key, item]) => (
            <div key={key} style={{ display: 'flex', gap: 8, padding: '2px 0' }}>
              <span style={{ color: 'var(--accent)' }}>{key}</span>
              <span style={{ color: 'var(--fg-4)' }}>:</span>
              <span style={{ color: 'var(--fg-1)', flex: 1, minWidth: 0, wordBreak: 'break-word' }}>{render(item, depth + 1)}</span>
            </div>
          ))}
        </div>
      )
    }

    if (typeof value === 'string') {
      return <span style={{ color: 'var(--amber)' }}>"{value}"</span>
    }

    return <span style={{ color: 'var(--fg-1)' }}>{String(value)}</span>
  }

  return (
    <div className="mono" style={{ fontSize: 11, background: 'var(--bg-inset)', border: '1px solid var(--line-hair)', borderRadius: 'var(--r-2)', padding: 12, lineHeight: 1.6, maxHeight: 220, overflow: 'auto' }}>
      {render(object)}
    </div>
  )
}

interface InspectorProps {
  scenario: UIScenario | null
  scenarios: UIScenario[]
  evidenceById: Record<string, UIEvidence[]>
  scoreComponentsById: Record<string, ScoreComponent[]>
  preparedById: Record<string, string>
  onSelect: (id: string) => void
  onFeedback: (id: string, kind: 'useful' | 'partial' | 'irrelevant') => void
  onPin: (id: string) => void
  pinnedIds: Set<string>
  onClose: () => void
}

export function Inspector({
  scenario,
  scenarios,
  evidenceById,
  scoreComponentsById,
  preparedById,
  onSelect,
  onFeedback,
  onPin,
  pinnedIds,
  onClose,
}: InspectorProps) {
  if (!scenario) {
    return (
      <div style={{ padding: 26, display: 'flex', flexDirection: 'column', gap: 14, color: 'var(--fg-3)' }}>
        <div className="editorial" style={{ fontSize: 24, color: 'var(--fg-2)', lineHeight: 1.25 }}>
          <em>Select a scenario</em>
        </div>
        <div style={{ fontSize: 13, lineHeight: 1.6 }}>
          Click any node in the frontier to see why Vaner is weighing it, what evidence supports it, the prepared context artefact, and how it contributes to the package.
        </div>
        <div style={{ height: 1, background: 'var(--line-hair)', margin: '8px 0' }} />
        <div className="mono" style={{ fontSize: 10.5, letterSpacing: 1, color: 'var(--fg-4)', lineHeight: 1.8 }}>
          KEYBOARD
          <br />
          <span style={{ color: 'var(--fg-2)' }}>←/→</span> prev/next scenario
          <br />
          <span style={{ color: 'var(--fg-2)' }}>U / P / X</span> useful / partial / irrelevant
          <br />
          <span style={{ color: 'var(--fg-2)' }}>•</span> pin to context
          <br />
          <span style={{ color: 'var(--fg-2)' }}>⌘K</span> command palette
          <br />
          <span style={{ color: 'var(--fg-2)' }}>Esc</span> deselect
        </div>
      </div>
    )
  }

  const parent = scenario.parent ? scenarios.find((item) => item.id === scenario.parent) : null
  const children = scenarios.filter((item) => item.parent === scenario.id)
  const evidence = evidenceById[scenario.id] ?? []
  const components = scoreComponentsById[scenario.id] ?? []
  const prepared = preparedById[scenario.id]
  const pinned = pinnedIds.has(scenario.id)

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '18px 18px 14px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
          <span className="mono" style={{ fontSize: 10, letterSpacing: 0.8, textTransform: 'uppercase', color: KIND_COLOR[scenario.kind], fontWeight: 500 }}>
            <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: 1, background: KIND_COLOR[scenario.kind], marginRight: 6 }} />
            {scenario.kind}
          </span>
          <div style={{ display: 'flex', gap: 4 }}>
            <button onClick={() => onPin(scenario.id)} style={iconBtn(pinned ? 'var(--amber)' : 'var(--fg-4)')} title={pinned ? 'Unpin' : 'Pin to context'}>
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.4">
                <path d="M6 1v7M3 8h6M4.5 8v3M7.5 8v3" />
              </svg>
            </button>
            <button onClick={onClose} style={iconBtn('var(--fg-4)')}>
              ×
            </button>
          </div>
        </div>
        <div style={{ fontSize: 18, color: 'var(--fg-1)', fontWeight: 500, letterSpacing: -0.2, marginBottom: 4, lineHeight: 1.3 }}>{scenario.title}</div>
        <div className="mono" style={{ fontSize: 11, color: 'var(--fg-4)' }}>
          {scenario.id} · {scenario.path}
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
          <Pill color="var(--amber)">{scenario.freshness}</Pill>
          <Pill>depth {scenario.depth}</Pill>
          {scenario.skill ? <Pill color="var(--accent)">{scenario.skill}</Pill> : null}
          {pinned ? <Pill color="var(--amber)" bg="color-mix(in oklch, var(--amber) 14%, var(--bg-2))">pinned</Pill> : null}
        </div>
      </div>

      <div className="scroll" style={{ flex: 1, overflow: 'auto' }}>
        <div style={{ padding: '6px 18px 18px' }}>
          <Verdict state={scenario.decisionState} reason={scenario.reason} packageId={scenario.decisionState === 'chosen' || scenario.decisionState === 'partial' ? 'pkg_live' : null} />
        </div>

        <FieldBlock title="Score breakdown" meta={`final ${scenario.score.toFixed(3)}`}>
          <ScoreBreakdown scenario={scenario} components={components} />
        </FieldBlock>

        <FieldBlock title="Evidence" meta={`${evidence.length} source${evidence.length !== 1 ? 's' : ''}`}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {evidence.map((item, index) => (
              <CodeEvidence key={index} {...item} />
            ))}
          </div>
        </FieldBlock>

        {prepared ? (
          <FieldBlock title="Prepared context" meta="artefact · JSON">
            <PreparedContext json={prepared} />
          </FieldBlock>
        ) : null}

        {scenario.entities.length ? (
          <FieldBlock title="Entities" meta={`${scenario.entities.length}`}>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {scenario.entities.map((entity) => (
                <Pill key={entity} color="var(--fg-1)">
                  {entity}
                </Pill>
              ))}
            </div>
          </FieldBlock>
        ) : null}

        <FieldBlock title="Lineage" meta={`${parent ? '1 parent · ' : 'root · '}${children.length} ${children.length === 1 ? 'child' : 'children'}`}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {parent ? (
              <button onClick={() => onSelect(parent.id)} style={lineageBtn}>
                <span style={{ color: 'var(--fg-4)', marginRight: 8 }}>▲</span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, letterSpacing: 0.8, textTransform: 'uppercase', color: KIND_COLOR[parent.kind] }}>{parent.kind}</span>
                <span style={{ color: 'var(--fg-1)', marginLeft: 10 }}>{parent.title}</span>
                <span className="mono" style={{ marginLeft: 'auto', color: KIND_COLOR[parent.kind] }}>
                  {parent.score.toFixed(3)}
                </span>
              </button>
            ) : (
              <div style={{ color: 'var(--fg-4)', fontSize: 12, padding: '4px 0' }}>Root scenario — spawned from initial signal sweep.</div>
            )}
            {children.map((child) => (
              <button key={child.id} onClick={() => onSelect(child.id)} style={lineageBtn}>
                <span style={{ color: 'var(--fg-4)', marginRight: 8 }}>▼</span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, letterSpacing: 0.8, textTransform: 'uppercase', color: KIND_COLOR[child.kind] }}>{child.kind}</span>
                <span style={{ color: 'var(--fg-1)', marginLeft: 10 }}>{child.title}</span>
                <span className="mono" style={{ marginLeft: 'auto', color: KIND_COLOR[child.kind] }}>
                  {child.score.toFixed(3)}
                </span>
              </button>
            ))}
          </div>
        </FieldBlock>
      </div>

      <div style={{ borderTop: '1px solid var(--line-hair)', padding: 14, background: 'var(--bg-1)' }}>
        <div className="mono" style={{ fontSize: 9.5, letterSpacing: 1.2, color: 'var(--fg-4)', marginBottom: 8 }}>
          STEER THE FRONTIER
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          {[
            { k: 'useful', l: 'Useful', c: 'var(--ok)' },
            { k: 'partial', l: 'Partial', c: 'var(--amber)' },
            { k: 'irrelevant', l: 'Irrelevant', c: 'var(--fg-4)' },
          ].map((button) => (
            <button
              key={button.k}
              onClick={() => onFeedback(scenario.id, button.k as 'useful' | 'partial' | 'irrelevant')}
              style={{
                flex: 1,
                padding: '10px 8px',
                borderRadius: 'var(--r-2)',
                border: '1px solid var(--line-1)',
                background: 'var(--bg-2)',
                color: button.c,
                fontFamily: 'var(--font-display)',
                fontSize: 11.5,
                fontWeight: 500,
                cursor: 'pointer',
                letterSpacing: 0.3,
                transition: 'all .15s',
              }}
            >
              {button.l}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

const iconBtn = (color: string): React.CSSProperties => ({
  background: 'transparent',
  border: 'none',
  color,
  cursor: 'pointer',
  padding: 4,
  lineHeight: 1,
  fontSize: 16,
  borderRadius: 'var(--r-1)',
})

const lineageBtn: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  width: '100%',
  padding: '9px 10px',
  background: 'var(--bg-2)',
  border: '1px solid var(--line-hair)',
  borderRadius: 'var(--r-2)',
  color: 'var(--fg-2)',
  cursor: 'pointer',
  fontFamily: 'var(--font-display)',
  fontSize: 12,
  textAlign: 'left',
  gap: 2,
}
