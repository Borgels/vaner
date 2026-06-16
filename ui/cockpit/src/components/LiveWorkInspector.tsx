import type { LiveWorkSnapshot } from '../types'

interface LiveWorkInspectorProps {
  snapshot: LiveWorkSnapshot | null
  live: boolean
  error: string | null
  title?: string
  onClose: () => void
}

export function LiveWorkInspector({ snapshot, live, error, title, onClose }: LiveWorkInspectorProps) {
  const events = [...(snapshot?.events ?? [])].sort((a, b) => b.ts - a.ts)
  const targets = snapshot?.prediction?.evidence_targets ?? snapshot?.prediction?.watched_sources ?? []
  return (
    <div className="scroll" style={{ height: '100%', overflow: 'auto', padding: 18 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'start' }}>
        <div style={{ minWidth: 0 }}>
          <div className="mono" style={eyebrowStyle}>LIVE WORK</div>
          <div style={{ fontFamily: 'var(--font-display)', fontSize: 18, color: 'var(--fg-1)' }}>
            {title ?? snapshot?.prediction?.display_label ?? snapshot?.prediction?.title ?? snapshot?.entity_id ?? 'Selected work'}
          </div>
          <div className="mono" style={{ fontSize: 10.5, color: live ? 'var(--ok)' : 'var(--fg-4)', marginTop: 6 }}>
            {live ? 'streaming' : 'snapshot'} · {snapshot?.status ?? 'loading'}
          </div>
        </div>
        <button type="button" onClick={onClose} style={buttonStyle} aria-label="Close live work inspector">
          x
        </button>
      </div>

      {error ? <div role="alert" style={{ ...cardStyle, color: 'var(--err)' }}>{error}</div> : null}

      <Section title="Current State">
        {snapshot?.summary ?? 'Loading live work state...'}
      </Section>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 8, marginTop: 14 }}>
        <Metric label="Status" value={snapshot?.status ?? 'loading'} />
        <Metric label="Active" value={snapshot?.active ? 'yes' : 'no'} />
        <Metric label="Worker" value={String(snapshot?.worker?.state ?? 'unknown')} />
        <Metric label="Queue" value={snapshot?.queue ? `${String(snapshot.queue.size ?? 0)}/${String(snapshot.queue.max_size ?? 0)}` : 'unknown'} />
      </div>

      <Section title="Targets">
        {targets.length ? (
          <div style={{ display: 'grid', gap: 5 }}>
            {targets.slice(0, 8).map((target) => (
              <div key={target} className="mono" style={{ color: 'var(--fg-3)', fontSize: 10.5, overflowWrap: 'anywhere' }}>{target}</div>
            ))}
          </div>
        ) : (
          <span style={{ color: 'var(--fg-4)' }}>No explicit targets recorded yet.</span>
        )}
      </Section>

      <div style={{ marginTop: 16 }}>
        <div className="mono" style={sectionTitle}>LIVE TIMELINE</div>
        {events.length ? events.map((event) => (
          <div key={event.event_id} style={cardStyle}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
              <span className="mono" style={{ color: 'var(--accent)', fontSize: 10.5 }}>{event.stage} · {event.status}</span>
              <span className="mono" style={{ color: 'var(--fg-4)', fontSize: 10 }}>{relativeAge(event.ts)}</span>
            </div>
            <div style={{ color: 'var(--fg-2)', fontSize: 12.5, lineHeight: 1.45, marginTop: 6 }}>{liveWorkEventSummary(event)}</div>
            {event.model || event.latency_ms || event.artifact_kind ? (
              <div className="mono" style={{ color: 'var(--fg-4)', fontSize: 10, marginTop: 6 }}>
                {[event.model, event.latency_ms ? `${Math.round(event.latency_ms)}ms` : null, event.artifact_kind].filter(Boolean).join(' · ')}
              </div>
            ) : null}
            {event.targets?.length ? <div className="mono" style={{ color: 'var(--fg-4)', fontSize: 10, marginTop: 5 }}>{event.targets.slice(0, 3).join(' · ')}</div> : null}
          </div>
        )) : (
          <div style={emptyStyle}>No live events for this item yet.</div>
        )}
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: 16 }}>
      <div className="mono" style={sectionTitle}>{title.toUpperCase()}</div>
      <div style={{ color: 'var(--fg-2)', fontSize: 12.5, lineHeight: 1.55 }}>{children}</div>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div style={cardStyle}>
      <div className="mono" style={{ color: 'var(--fg-4)', fontSize: 10 }}>{label}</div>
      <div style={{ color: 'var(--fg-1)', fontSize: 13, marginTop: 4, overflowWrap: 'anywhere' }}>{value}</div>
    </div>
  )
}

function relativeAge(epoch: number): string {
  if (!epoch) return 'unknown'
  const delta = Math.max(0, Date.now() / 1000 - epoch)
  if (delta < 60) return `${Math.round(delta)}s ago`
  if (delta < 3600) return `${Math.round(delta / 60)}m ago`
  if (delta < 86400) return `${Math.round(delta / 3600)}h ago`
  return `${Math.round(delta / 86400)}d ago`
}

function liveWorkEventSummary(event: LiveWorkSnapshot['events'][number]): string {
  const payload = event.metadata?.payload
  if (!isRecord(payload)) {
    return event.summary
  }
  const tokensUsed = Number(payload.tokens_used)
  const tokenBudget = Number(payload.token_budget)
  if (!Number.isFinite(tokensUsed) || !Number.isFinite(tokenBudget) || tokenBudget <= 0) {
    return event.summary
  }
  const complete = Number(payload.scenarios_complete)
  const over = Math.max(0, Math.round(tokensUsed - tokenBudget))
  const base = `Progress: ${Math.round(tokensUsed)} tokens used / ${Math.round(tokenBudget)} token target`
  const suffix = Number.isFinite(complete) ? `, ${Math.round(complete)} scenarios complete` : ''
  return over > 0 ? `${base} (${over} over target)${suffix}.` : `${base}${suffix}.`
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

const eyebrowStyle: React.CSSProperties = { fontSize: 10, letterSpacing: 1.2, color: 'var(--fg-4)', marginBottom: 8 }
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
const emptyStyle: React.CSSProperties = { color: 'var(--fg-4)', fontSize: 12 }
