import type { PipelineEvent } from '../api/usePipelineEvents'
import type { PreparedWorkCard, PredictionSummary, UIEvidence, UIScenario } from '../types'
import { EventStreamPanel } from './EventStreamPanel'
import { PreparedWorkPanel, preparedKindLabel } from './PreparedWorkPanel'

interface PreparedWorkViewProps {
  cards: PreparedWorkCard[]
  loading: boolean
  error: string | null
  selectedId: string | null
  onSelect: (id: string) => void
  onCardsChange: (cards: PreparedWorkCard[]) => void
  onAction: (message: string) => void
}

export function PreparedWorkView({
  cards,
  loading,
  error,
  selectedId,
  onSelect,
  onCardsChange,
  onAction,
}: PreparedWorkViewProps) {
  return (
    <ViewShell eyebrow="Prepared Work" title="What Vaner has ready right now">
      <PreparedWorkPanel
        variant="main"
        cards={cards}
        loading={loading}
        error={error}
        selectedId={selectedId}
        onSelect={onSelect}
        onCardsChange={onCardsChange}
        onAction={onAction}
      />
    </ViewShell>
  )
}

interface NowViewProps {
  cards: PreparedWorkCard[]
  scenarios: UIScenario[]
  selectedWorkId: string | null
  selectedScenarioId: string | null
  onSelectWork: (id: string) => void
  onSelectScenario: (id: string) => void
}

export function NowView({
  cards,
  scenarios,
  selectedWorkId,
  selectedScenarioId,
  onSelectWork,
  onSelectScenario,
}: NowViewProps) {
  const workItems = cards.slice(0, 6)
  const fallbackScenarios = scenarios.filter((scenario) => !workItems.some((card) => card.source_id === scenario.id)).slice(0, 5)
  const primaryWork = workItems[0]
  const primaryScenario = primaryWork ? null : fallbackScenarios[0]
  const secondaryWork = workItems.slice(1, 5)
  const secondaryScenarios = primaryWork ? fallbackScenarios.slice(0, Math.max(0, 5 - secondaryWork.length)) : fallbackScenarios.slice(1, 5)

  return (
    <ViewShell eyebrow="Now" title="What matters next">
      <div className="scroll" style={viewScrollStyle}>
        {primaryWork ? (
          <OpportunityCard
            selected={selectedWorkId === primaryWork.id}
            title={primaryWork.title}
            type={preparedKindLabel(primaryWork.kind)}
            confidence={primaryWork.confidence_label}
            freshness={primaryWork.freshness_label}
            evidence={`${primaryWork.evidence_count} sources`}
            target={primaryWork.target_label}
            reason={primaryWork.summary}
            action={primaryWork.primary_action?.label ?? 'Inspect'}
            primary
            onClick={() => onSelectWork(primaryWork.id)}
          />
        ) : primaryScenario ? (
          <ScenarioOpportunityCard
            scenario={primaryScenario}
            selected={selectedScenarioId === primaryScenario.id}
            primary
            onClick={() => onSelectScenario(primaryScenario.id)}
          />
        ) : (
          <EmptyState text="No opportunities are ready yet." />
        )}

        <div style={{ display: 'grid', gap: 10, marginTop: 14 }}>
          {secondaryWork.map((card) => (
            <OpportunityCard
              key={card.id}
              selected={selectedWorkId === card.id}
              title={card.title}
              type={preparedKindLabel(card.kind)}
              confidence={card.confidence_label}
              freshness={card.freshness_label}
              evidence={`${card.evidence_count} sources`}
              target={card.target_label}
              reason={card.summary}
              action={card.primary_action?.label ?? 'Inspect'}
              onClick={() => onSelectWork(card.id)}
            />
          ))}
          {secondaryScenarios.map((scenario) => (
            <ScenarioOpportunityCard
              key={scenario.id}
              scenario={scenario}
              selected={selectedScenarioId === scenario.id}
              onClick={() => onSelectScenario(scenario.id)}
            />
          ))}
        </div>
      </div>
    </ViewShell>
  )
}

interface TimelineViewProps {
  events: PipelineEvent[]
  live: boolean
  pendingLlm: number
  onSelect: (id: string) => void
}

export function TimelineView({ events, live, pendingLlm, onSelect }: TimelineViewProps) {
  return (
    <ViewShell eyebrow="Timeline" title="How Vaner got here">
      <EventStreamPanel
        title="TIMELINE"
        subtitle="Live workspace and preparation activity"
        events={events}
        onSelect={onSelect}
        live={live}
        pendingLlm={pendingLlm}
        collapseHeartbeats={false}
      />
    </ViewShell>
  )
}

interface BoardViewProps {
  cards: PreparedWorkCard[]
  predictions: PredictionSummary[]
  scenarios: UIScenario[]
  selectedWorkId: string | null
  selectedScenarioId: string | null
  onSelectWork: (id: string) => void
  onSelectScenario: (id: string) => void
}

export function BoardView({
  cards,
  predictions,
  scenarios,
  selectedWorkId,
  selectedScenarioId,
  onSelectWork,
  onSelectScenario,
}: BoardViewProps) {
  const warming = predictions.filter((prediction) => !['ready', 'stale'].includes(String(prediction.readiness ?? '')))
  const readyScenarios = scenarios.filter((scenario) => scenario.freshness === 'fresh').slice(0, 8)
  const adoptedOrDismissed = cards.filter((card) =>
    card.secondary_actions.some((action) => action.kind === 'dismiss') && card.badge.toLowerCase().includes('dismiss'),
  )
  const prepared = cards.filter((card) => !adoptedOrDismissed.includes(card))
  const newItems = scenarios.filter((scenario) => scenario.freshness !== 'fresh').slice(0, 8)

  return (
    <ViewShell eyebrow="Board" title="Lifecycle at a glance">
      <div className="scroll" style={{ ...viewScrollStyle, display: 'grid', gridTemplateColumns: 'repeat(5, minmax(180px, 1fr))', gap: 10, alignItems: 'start' }}>
        <BoardColumn title="New">
          {newItems.map((scenario) => (
            <BoardScenarioCard key={scenario.id} scenario={scenario} selected={selectedScenarioId === scenario.id} onClick={() => onSelectScenario(scenario.id)} />
          ))}
        </BoardColumn>
        <BoardColumn title="Warming">
          {warming.slice(0, 8).map((prediction) => (
            <MiniCard key={prediction.id} selected={false} title={prediction.title ?? prediction.prompt ?? prediction.id} meta={`${prediction.readiness ?? 'warming'} · ${formatConfidence(prediction.confidence)}`} onClick={() => undefined} />
          ))}
        </BoardColumn>
        <BoardColumn title="Ready">
          {readyScenarios.map((scenario) => (
            <BoardScenarioCard key={scenario.id} scenario={scenario} selected={selectedScenarioId === scenario.id} onClick={() => onSelectScenario(scenario.id)} />
          ))}
        </BoardColumn>
        <BoardColumn title="Prepared">
          {prepared.slice(0, 10).map((card) => (
            <BoardPreparedCard key={card.id} card={card} selected={selectedWorkId === card.id} onClick={() => onSelectWork(card.id)} />
          ))}
        </BoardColumn>
        <BoardColumn title="Adopted / Dismissed">
          {adoptedOrDismissed.slice(0, 10).map((card) => (
            <BoardPreparedCard key={card.id} card={card} selected={selectedWorkId === card.id} onClick={() => onSelectWork(card.id)} />
          ))}
        </BoardColumn>
      </div>
    </ViewShell>
  )
}

interface EvidenceViewProps {
  cards: PreparedWorkCard[]
  scenarios: UIScenario[]
  evidenceById: Record<string, UIEvidence[]>
  selectedWorkId: string | null
  selectedScenarioId: string | null
  onSelectWork: (id: string) => void
  onSelectScenario: (id: string) => void
}

export function EvidenceView({
  cards,
  scenarios,
  evidenceById,
  selectedWorkId,
  selectedScenarioId,
  onSelectWork,
  onSelectScenario,
}: EvidenceViewProps) {
  return (
    <ViewShell eyebrow="Evidence" title="Why Vaner believes it">
      <div className="scroll" style={viewScrollStyle}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr>
              {['Item', 'Sources / Areas', 'Activity', 'Confidence', 'Prepared Output', 'Feedback'].map((label) => (
                <th key={label} style={thStyle}>{label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {cards.map((card) => (
              <tr key={card.id} onClick={() => onSelectWork(card.id)} style={rowStyle(selectedWorkId === card.id)}>
                <td style={tdStyle}>{card.title}<Meta>{preparedKindLabel(card.kind)}</Meta></td>
                <td style={tdStyle}>{card.target_label}<Meta>{card.evidence_count} evidence refs</Meta></td>
                <td style={tdStyle}>{relativeAge(card.updated_at)}</td>
                <td style={tdStyle}>{card.confidence_label}<Meta>{card.freshness_label}</Meta></td>
                <td style={tdStyle}>{card.summary}</td>
                <td style={tdStyle}>{card.secondary_actions.some((action) => action.kind === 'feedback') ? 'available' : 'none'}</td>
              </tr>
            ))}
            {scenarios.map((scenario) => {
              const evidence = evidenceById[scenario.id] ?? []
              return (
                <tr key={scenario.id} onClick={() => onSelectScenario(scenario.id)} style={rowStyle(selectedScenarioId === scenario.id)}>
                  <td style={tdStyle}>{scenario.title}<Meta>{scenario.kind}</Meta></td>
                  <td style={tdStyle}>{scenario.entities.slice(0, 2).join(', ') || scenario.path || 'none'}<Meta>{evidence.length} evidence refs</Meta></td>
                  <td style={tdStyle}>{scenario.freshness}</td>
                  <td style={tdStyle}>{scenario.score.toFixed(3)}</td>
                  <td style={tdStyle}>{scenario.reason}</td>
                  <td style={tdStyle}>{scenario.pinned ? 'pinned' : 'open'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </ViewShell>
  )
}

function ScenarioOpportunityCard({ scenario, selected, primary = false, onClick }: { scenario: UIScenario; selected: boolean; primary?: boolean; onClick: () => void }) {
  return (
    <OpportunityCard
      selected={selected}
      title={scenario.title}
      type={scenario.kind}
      confidence={scenario.score.toFixed(3)}
      freshness={scenario.freshness}
      evidence={`${scenario.entities.length || (scenario.path ? 1 : 0)} areas`}
      target={scenario.entities.slice(0, 2).join(', ') || scenario.path || 'workspace'}
      reason={scenario.reason}
      action="Inspect"
      primary={primary}
      onClick={onClick}
    />
  )
}

function OpportunityCard({
  selected,
  title,
  type,
  confidence,
  freshness,
  evidence,
  target,
  reason,
  action,
  primary = false,
  onClick,
}: {
  selected: boolean
  title: string
  type: string
  confidence: string
  freshness: string
  evidence: string
  target: string
  reason: string
  action: string
  primary?: boolean
  onClick: () => void
}) {
  return (
    <button type="button" onClick={onClick} style={opportunityStyle(selected, primary)}>
      <span style={{ display: 'flex', alignItems: 'start', justifyContent: 'space-between', gap: 12 }}>
        <span style={{ minWidth: 0 }}>
          <span style={{ display: 'block', fontFamily: 'var(--font-display)', fontSize: primary ? 20 : 14, color: 'var(--fg-1)', lineHeight: 1.2 }}>{title}</span>
          <span style={{ display: 'block', color: 'var(--fg-3)', fontSize: 12.5, marginTop: 7, lineHeight: 1.45 }}>{reason}</span>
        </span>
        <span style={badgeStyle}>{action}</span>
      </span>
      <span className="mono" style={metaRowStyle}>
        {type} · {confidence} · {freshness} · {evidence} · {target}
      </span>
    </button>
  )
}

function BoardColumn({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{ background: 'var(--bg-1)', border: '1px solid var(--line-1)', borderRadius: 'var(--r-2)', minHeight: 260, overflow: 'hidden' }}>
      <header className="mono" style={{ padding: '10px 12px', borderBottom: '1px solid var(--line-hair)', color: 'var(--fg-3)', fontSize: 10, letterSpacing: 1 }}>
        {title}
      </header>
      <div style={{ padding: 8, display: 'grid', gap: 8 }}>{children}</div>
    </section>
  )
}

function BoardPreparedCard({ card, selected, onClick }: { card: PreparedWorkCard; selected: boolean; onClick: () => void }) {
  return <MiniCard selected={selected} title={card.title} meta={`${preparedKindLabel(card.kind)} · ${card.confidence_label} · ${card.freshness_label} · ${card.evidence_count} sources`} onClick={onClick} />
}

function BoardScenarioCard({ scenario, selected, onClick }: { scenario: UIScenario; selected: boolean; onClick: () => void }) {
  return <MiniCard selected={selected} title={scenario.title} meta={`${scenario.kind} · ${scenario.score.toFixed(3)} · ${scenario.freshness}`} onClick={onClick} />
}

function MiniCard({ selected, title, meta, onClick }: { selected: boolean; title: string; meta: string; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} style={{ textAlign: 'left', border: selected ? '1px solid var(--accent)' : '1px solid var(--line-hair)', borderRadius: 'var(--r-1)', background: selected ? 'color-mix(in oklch, var(--accent) 8%, var(--bg-inset))' : 'var(--bg-inset)', color: 'var(--fg-1)', padding: 10, cursor: 'pointer' }}>
      <span style={{ display: 'block', fontSize: 12.5, lineHeight: 1.25 }}>{title}</span>
      <span className="mono" style={{ display: 'block', color: 'var(--fg-4)', fontSize: 10, marginTop: 6 }}>{meta}</span>
    </button>
  )
}

function ViewShell({ eyebrow, title, children }: { eyebrow: string; title: string; children: React.ReactNode }) {
  return (
    <div style={{ height: '100%', minHeight: 0, display: 'grid', gridTemplateRows: 'auto minmax(0, 1fr)', background: 'var(--bg-0)' }}>
      <header style={{ padding: '14px 18px 12px', borderBottom: '1px solid var(--line-hair)', background: 'var(--bg-1)' }}>
        <div className="mono" style={{ fontSize: 10, letterSpacing: 1.2, color: 'var(--fg-4)' }}>{eyebrow}</div>
        <div style={{ fontFamily: 'var(--font-display)', color: 'var(--fg-1)', fontSize: 18, marginTop: 3 }}>{title}</div>
      </header>
      <div style={{ minHeight: 0, overflow: 'hidden' }}>{children}</div>
    </div>
  )
}

function EmptyState({ text }: { text: string }) {
  return <div style={{ color: 'var(--fg-4)', fontSize: 13, padding: 18 }}>{text}</div>
}

function Meta({ children }: { children: React.ReactNode }) {
  return <div className="mono" style={{ color: 'var(--fg-4)', fontSize: 10.5, marginTop: 4 }}>{children}</div>
}

function formatConfidence(value: number | undefined): string {
  return typeof value === 'number' ? value.toFixed(2) : 'unknown'
}

function relativeAge(epoch: number): string {
  if (!epoch) return 'unknown'
  const delta = Math.max(0, Date.now() / 1000 - epoch)
  if (delta < 60) return `${Math.round(delta)}s ago`
  if (delta < 3600) return `${Math.round(delta / 60)}m ago`
  if (delta < 86400) return `${Math.round(delta / 3600)}h ago`
  return `${Math.round(delta / 86400)}d ago`
}

const viewScrollStyle: React.CSSProperties = {
  height: '100%',
  overflow: 'auto',
  padding: 18,
}

const badgeStyle: React.CSSProperties = {
  border: '1px solid var(--accent)',
  borderRadius: 'var(--r-1)',
  color: 'var(--fg-1)',
  background: 'var(--accent-bg)',
  padding: '5px 8px',
  fontSize: 11,
  whiteSpace: 'nowrap',
}

const metaRowStyle: React.CSSProperties = {
  display: 'block',
  marginTop: 12,
  color: 'var(--fg-4)',
  fontSize: 10.5,
  letterSpacing: 0.2,
}

function opportunityStyle(selected: boolean, primary: boolean): React.CSSProperties {
  return {
    width: '100%',
    textAlign: 'left',
    border: selected ? '1px solid var(--accent)' : '1px solid var(--line-1)',
    borderRadius: 'var(--r-2)',
    background: selected ? 'color-mix(in oklch, var(--accent) 9%, var(--bg-1))' : 'var(--bg-1)',
    padding: primary ? 18 : 13,
    cursor: 'pointer',
  }
}

const thStyle: React.CSSProperties = {
  textAlign: 'left',
  color: 'var(--fg-4)',
  fontFamily: 'var(--font-mono)',
  fontSize: 10,
  letterSpacing: 0.8,
  padding: '9px 10px',
  borderBottom: '1px solid var(--line-1)',
}

const tdStyle: React.CSSProperties = {
  padding: '10px',
  borderBottom: '1px solid var(--line-hair)',
  color: 'var(--fg-2)',
  verticalAlign: 'top',
  lineHeight: 1.35,
}

function rowStyle(selected: boolean): React.CSSProperties {
  return {
    background: selected ? 'color-mix(in oklch, var(--accent) 8%, var(--bg-1))' : 'transparent',
    cursor: 'pointer',
  }
}
