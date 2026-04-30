import type { UIEvidence, UIScenario } from '../types'

export interface ScoreComponent {
  label: string
  value: number
  description?: string
}

interface InspectorProps {
  scenario: UIScenario | null
  scenarios: UIScenario[]
  evidenceById: Record<string, UIEvidence[]>
  scoreComponentsById: Record<string, ScoreComponent[]>
  preparedById: Record<string, string>
  onSelect: (id: string) => void
  onFeedback: (id: string, result: 'useful' | 'partial' | 'irrelevant') => void
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
      <div style={{ padding: 18, color: 'var(--fg-4)' }}>
        <div className="mono" style={{ fontSize: 10, letterSpacing: 1.2, marginBottom: 10 }}>
          INSPECTOR
        </div>
        <div style={{ fontSize: 13 }}>No scenario selected.</div>
      </div>
    )
  }

  const evidence = evidenceById[scenario.id] ?? []
  const scores = scoreComponentsById[scenario.id] ?? []
  const prepared = preparedById[scenario.id]

  return (
    <div className="scroll" style={{ height: '100%', overflow: 'auto', padding: 18 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'start' }}>
        <div>
          <div className="mono" style={{ fontSize: 10, letterSpacing: 1.2, color: 'var(--fg-4)', marginBottom: 8 }}>
            SCENARIO INSPECTOR
          </div>
          <div style={{ fontFamily: 'var(--font-display)', fontSize: 18, color: 'var(--fg-1)' }}>{scenario.title}</div>
          <div className="mono" style={{ fontSize: 10.5, color: 'var(--fg-4)', marginTop: 6 }}>
            {scenario.id} · {scenario.kind} · score {scenario.score.toFixed(3)}
          </div>
        </div>
        <button type="button" onClick={onClose} style={buttonStyle} aria-label="Close inspector">
          x
        </button>
      </div>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 14 }}>
        <button type="button" onClick={() => onFeedback(scenario.id, 'useful')} style={buttonStyle}>useful</button>
        <button type="button" onClick={() => onFeedback(scenario.id, 'partial')} style={buttonStyle}>partial</button>
        <button type="button" onClick={() => onFeedback(scenario.id, 'irrelevant')} style={buttonStyle}>irrelevant</button>
        <button type="button" onClick={() => onPin(scenario.id)} style={buttonStyle}>
          {pinnedIds.has(scenario.id) ? 'unpin' : 'pin'}
        </button>
      </div>

      <Section title="Reason">{scenario.reason || 'No reason recorded.'}</Section>
      <Section title="Path">{scenario.path || 'No path recorded.'}</Section>

      <div style={{ marginTop: 16 }}>
        <div className="mono" style={sectionTitle}>EVIDENCE</div>
        {evidence.length ? evidence.map((item, index) => (
          <div key={`${item.file}-${index}`} style={cardStyle}>
            <div className="mono" style={{ color: 'var(--accent)', fontSize: 10.5 }}>{item.file || 'unknown'}</div>
            <div style={{ color: 'var(--fg-3)', fontSize: 12, marginTop: 5 }}>{item.note || 'No excerpt.'}</div>
          </div>
        )) : <div style={emptyStyle}>No evidence linked yet.</div>}
      </div>

      <div style={{ marginTop: 16 }}>
        <div className="mono" style={sectionTitle}>SCORE</div>
        {scores.length ? scores.map((item) => (
          <div key={item.label} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '5px 0' }}>
            <span>{item.label}</span>
            <span className="mono">{Number(item.value).toFixed(3)}</span>
          </div>
        )) : <div style={emptyStyle}>No score breakdown.</div>}
      </div>

      {prepared ? (
        <div style={{ marginTop: 16 }}>
          <div className="mono" style={sectionTitle}>PREPARED CONTEXT</div>
          <pre style={preStyle}>{prepared}</pre>
        </div>
      ) : null}

      <div style={{ marginTop: 16 }}>
        <div className="mono" style={sectionTitle}>NEARBY</div>
        {scenarios.slice(0, 8).map((item) => (
          <button key={item.id} type="button" onClick={() => onSelect(item.id)} style={{ ...buttonStyle, display: 'block', width: '100%', textAlign: 'left', marginBottom: 6 }}>
            {item.title}
          </button>
        ))}
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: 16 }}>
      <div className="mono" style={sectionTitle}>{title.toUpperCase()}</div>
      <div style={{ fontSize: 12.5, color: 'var(--fg-2)', lineHeight: 1.55 }}>{children}</div>
    </div>
  )
}

const sectionTitle: React.CSSProperties = { fontSize: 10, letterSpacing: 1.1, color: 'var(--fg-4)', marginBottom: 8 }
const buttonStyle: React.CSSProperties = {
  border: '1px solid var(--line-1)',
  background: 'var(--bg-2)',
  color: 'var(--fg-2)',
  borderRadius: 'var(--r-1)',
  padding: '6px 9px',
  cursor: 'pointer',
}
const cardStyle: React.CSSProperties = {
  border: '1px solid var(--line-hair)',
  background: 'var(--bg-inset)',
  borderRadius: 'var(--r-2)',
  padding: 10,
  marginBottom: 8,
}
const preStyle: React.CSSProperties = { ...cardStyle, color: 'var(--fg-2)', whiteSpace: 'pre-wrap', overflow: 'auto', fontSize: 11 }
const emptyStyle: React.CSSProperties = { color: 'var(--fg-4)', fontSize: 12 }
