import type { PipelineEvent } from '../api/usePipelineEvents'
import type {
  ActiveWorkPayload,
  FocusStatePayload,
  FocusRoutePayload,
  JobsStatusPayload,
  PlanDraftSummary,
  PreparedWorkCard,
  PredictionSummary,
  RecentActivityPayload,
  SignalCapabilitiesPayload,
  SourcesPermissionsPayload,
  UIEvidence,
  UIScenario,
} from '../types'
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

interface FocusViewProps {
  focus: FocusStatePayload | null
  route: FocusRoutePayload | null
  jobs: JobsStatusPayload | null
  activity: RecentActivityPayload | null
  predictions: PredictionSummary[]
  activeWork: ActiveWorkPayload | null
  sources: SourcesPermissionsPayload | null
  capabilities: SignalCapabilitiesPayload | null
  loading: boolean
  error: string | null
  onSelectPrediction?: (id: string) => void
}

export function FocusView({
  focus,
  route,
  jobs,
  activity,
  predictions,
  activeWork,
  sources,
  capabilities,
  loading,
  error,
  onSelectPrediction,
}: FocusViewProps) {
  const effective = route?.effective_route
  const focusState = focus ?? route?.diagnostics?.focus ?? null
  const activeWorkspace = effective?.workspace ?? focusState?.workspaces?.find((workspace) => workspace.id === focusState.active_workspace_id)
  const activeClient = effective?.client ?? focusState?.detected_clients?.find((client) => client.id === focusState.active_client_id)
  const focusJobs = jobs?.jobs ?? []
  const promptSignals = activity?.items ?? []
  const routeStatus = focusState?.status ?? (effective ? 'active route' : 'unknown')
  const proactiveLabel = typeof focusState?.proactive_allowed === 'boolean' ? (focusState.proactive_allowed ? 'allowed' : 'blocked') : 'unknown'
  const proactiveTone = typeof focusState?.proactive_allowed === 'boolean' ? (focusState.proactive_allowed ? 'ok' : 'warn') : 'muted'
  const worker = jobs?.worker
  const queue = jobs?.queue
  const codexCapability = capabilities?.composer_adapters?.find((adapter) => adapter.host_app === 'codex-cli')
  const globalPlans = sources?.sources?.global_client_plans
  const directionPredictions = predictions
    .filter((prediction) => prediction.trust_status !== 'invalidated')
    .filter((prediction) => isDirectionPrediction(prediction))
    .slice(0, 8)
  const historyPredictions = predictions
    .filter((prediction) => prediction.trust_status !== 'invalidated')
    .filter((prediction) => {
      const text = `${prediction.source_label ?? ''} ${prediction.spec?.source ?? ''} ${prediction.title ?? ''} ${prediction.ui_summary ?? ''}`.toLowerCase()
      return text.includes('history') || text.includes('codex') || text.includes('recent') || text.includes('chat') || text.includes('goal')
    })
    .slice(0, 6)
  const shownPredictions = historyPredictions.length ? historyPredictions : predictions.slice(0, 6)

  return (
    <ViewShell eyebrow="Auto Focus" title="What Vaner is listening to">
      <div className="scroll" style={viewScrollStyle}>
        {error ? <div role="alert" style={{ ...focusBannerStyle, borderColor: 'var(--err)', color: 'var(--err)' }}>{error}</div> : null}
        {loading && !route ? <div role="status" style={focusBannerStyle}>Loading focus state...</div> : null}

        {activeWork ? (
          <section style={focusSectionStyle}>
            <SectionHeader title="Active Work" meta={`${activeWork.loop} · ${activeWork.phase}`} />
            <FocusNote>{activeWork.summary}</FocusNote>
            <div style={{ display: 'grid', gap: 8, marginTop: 8 }}>
              <FocusField label="Predictions" value={`${activeWork.prediction_count ?? 0}`} />
              <FocusField label="Prepared" value={`${activeWork.prepared_work_count ?? 0}`} />
              {activeWork.plan_draft ? <FocusField label="Plan" value={activeWork.plan_draft.title} tone="ok" /> : null}
              {activeWork.utilization?.gpu?.available ? (
                <FocusField
                  label="GPU"
                  value={`${String(activeWork.utilization.gpu.name ?? 'GPU')} · ${Math.round(Number(activeWork.utilization.gpu.utilization ?? 0) * 100)}%`}
                />
              ) : null}
            </div>
          </section>
        ) : null}

        <section style={focusSectionStyle}>
          <SectionHeader title="Current Direction" meta={`${directionPredictions.length} preparing`} />
          {directionPredictions.length ? (
            <>
              <FocusNote>{directionSummary(directionPredictions)}</FocusNote>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10, marginTop: 10 }}>
                {directionPredictions.slice(0, 6).map((prediction) => (
                  <PredictionMiniCard key={prediction.id} prediction={prediction} onSelect={onSelectPrediction} />
                ))}
              </div>
            </>
          ) : (
            <EmptyState text="No likely next work is visible for this focus route yet." />
          )}
        </section>

        <div style={focusGridStyle}>
          <FocusPanel title="Listening">
            <FocusField label="Status" value={routeStatus} tone={focusState?.status === 'active' || effective ? 'ok' : 'muted'} />
            <FocusField label="Client" value={activeClient?.display_name ?? focusState?.active_client_id ?? 'none'} />
            <FocusField label="Workspace" value={activeWorkspace?.display_name ?? focusState?.active_workspace_id ?? 'none'} />
            <FocusField label="Selection" value={focusState?.selected_by ?? effective?.selected_by ?? 'unknown'} />
            <FocusField label="Proactive" value={proactiveLabel} tone={proactiveTone} />
            {focusState?.explanation ? <FocusNote>{focusState.explanation}</FocusNote> : null}
          </FocusPanel>

          <FocusPanel title="Route">
            <FocusField label="Path" value={activeWorkspace?.canonical_path ?? 'none'} />
            <FocusField label="Policy" value={effective?.workspace_policy ?? 'auto'} />
            <FocusField label="Mode" value={effective?.resource_mode ?? focusState?.resource_mode ?? 'unknown'} />
            <FocusField label="Device" value={effective?.device ?? 'unknown'} />
            <FocusField label="Model" value={effective?.backend?.model ?? 'unknown'} />
            <FocusField label="Runtime" value={effective?.backend?.base_url ?? 'unknown'} />
          </FocusPanel>

          <FocusPanel title="Job Gate">
            {worker ? (
              <div style={focusJobStyle}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                  <span style={{ color: 'var(--fg-1)' }}>precompute worker</span>
                  <span className="mono" style={{ color: worker.state === 'running' ? 'var(--ok)' : 'var(--fg-4)', fontSize: 10 }}>{worker.state ?? 'unknown'}</span>
                </div>
                <Meta>{[worker.phase, worker.defer_reason, queue ? `queue ${queue.size ?? 0}/${queue.max_size ?? 0}` : null].filter(Boolean).join(' · ')}</Meta>
              </div>
            ) : null}
            {focusJobs.length ? (
              focusJobs.slice(0, 5).map((job) => (
                <div key={job.id} style={focusJobStyle}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                    <span style={{ color: 'var(--fg-1)' }}>{job.job_class}</span>
                    <span className="mono" style={{ color: job.status === 'running' ? 'var(--ok)' : 'var(--fg-4)', fontSize: 10 }}>{job.status}</span>
                  </div>
                  <Meta>{[job.priority, job.defer_reason, job.explanation].filter(Boolean).join(' · ') || job.id}</Meta>
                </div>
              ))
            ) : (
              <EmptyState text="No focus jobs are queued." />
            )}
          </FocusPanel>
        </div>

        <section style={focusSectionStyle}>
          <SectionHeader title="Live Intent Capability" meta={codexCapability ? `${codexCapability.host_app} ${codexCapability.level}` : 'unknown'} />
          {codexCapability ? (
            <div style={signalStyle}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                <span style={{ color: 'var(--fg-1)' }}>{codexCapability.host_app}</span>
                <span className="mono" style={{ color: codexCapability.official_pre_enter_available ? 'var(--ok)' : 'var(--amber)', fontSize: 10 }}>
                  {codexCapability.official_pre_enter_available ? 'pre-enter available' : 'submitted only'}
                </span>
              </div>
              <Meta>{codexCapability.reason}</Meta>
            </div>
          ) : (
            <EmptyState text="No composer adapter capability report has been loaded." />
          )}
        </section>

        <section style={focusSectionStyle}>
          <SectionHeader
            title="Source Permissions"
            meta={globalPlans ? `${globalPlans.accepted_count ?? 0} matched · ${globalPlans.skipped_count ?? 0} skipped` : 'not loaded'}
          />
          {globalPlans ? (
            <div style={signalStyle}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                <span style={{ color: 'var(--fg-1)' }}>Global client plans</span>
                <span className="mono" style={{ color: globalPlans.enabled ? 'var(--ok)' : 'var(--fg-4)', fontSize: 10 }}>
                  {globalPlans.enabled ? 'enabled' : 'opt-in off'}
                </span>
              </div>
              <Meta>{[globalPlans.privacy_zone, ...(globalPlans.selected_clients ?? [])].filter(Boolean).join(' · ')}</Meta>
              {globalPlans.accepted_examples?.length ? (
                <Meta>Matched: {globalPlans.accepted_examples.slice(0, 2).map((item) => item.display_path ?? item.path).join(' · ')}</Meta>
              ) : null}
            </div>
          ) : (
            <EmptyState text="Source permission state is not available yet." />
          )}
        </section>

        <section style={focusSectionStyle}>
          <SectionHeader title="Current Thread Signals" meta={`${promptSignals.length} recent Codex prompts`} />
          {promptSignals.length ? (
            <div style={{ display: 'grid', gap: 8 }}>
              {promptSignals.map((item) => (
                <div key={item.id} style={signalStyle}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
                    <span className="mono" style={{ color: 'var(--fg-4)', fontSize: 10 }}>
                      {[item.host_app, item.source, item.turn_id ?? item.session_id].filter(Boolean).join(' · ')}
                    </span>
                    <span className="mono" style={{ color: 'var(--fg-4)', fontSize: 10 }}>{relativeAge(item.timestamp)}</span>
                  </div>
                  <div style={{ color: 'var(--fg-2)', fontSize: 12.5, lineHeight: 1.45, marginTop: 6 }}>{item.query_text}</div>
                  {item.selected_paths?.length ? <Meta>{item.selected_paths.slice(0, 3).join(' · ')}</Meta> : null}
                </div>
              ))}
            </div>
          ) : (
            <EmptyState text="No recent Codex prompt signals have been recorded." />
          )}
        </section>

        <section style={focusSectionStyle}>
          <SectionHeader title="Prepared Context" meta={`${shownPredictions.length} visible`} />
          {shownPredictions.length ? (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
              {shownPredictions.map((prediction) => (
                <MiniCard
                  key={prediction.id}
                  selected={false}
                  title={prediction.display_label ?? prediction.title ?? prediction.label ?? prediction.prompt ?? prediction.spec?.label ?? prediction.id}
                  meta={`${prediction.source_label ?? prediction.spec?.source ?? 'prepared context'} · ${prediction.snapshot_freshness ?? prediction.readiness ?? prediction.run?.readiness ?? 'unknown'} · ${prediction.match_state ?? formatConfidence(prediction.confidence)}`}
                  onClick={() => onSelectPrediction?.(prediction.id)}
                />
              ))}
            </div>
          ) : (
            <EmptyState text="No prepared context is visible for this focus route yet." />
          )}
        </section>

        {focusState?.why_not?.length ? (
          <section style={focusSectionStyle}>
            <SectionHeader title="Blocked Reasons" meta={`${focusState.why_not.length}`} />
            {focusState.why_not.map((reason, index) => (
              <FocusNote key={`${reason.reason_code ?? 'reason'}-${index}`}>{[reason.reason_code, reason.message, reason.action].filter(Boolean).join(' · ')}</FocusNote>
            ))}
          </section>
        ) : null}
      </div>
    </ViewShell>
  )
}

interface NowViewProps {
  cards: PreparedWorkCard[]
  predictions: PredictionSummary[]
  scenarios: UIScenario[]
  activeWork: ActiveWorkPayload | null
  selectedWorkId: string | null
  selectedScenarioId: string | null
  onSelectWork: (id: string) => void
  onSelectScenario: (id: string) => void
  onSelectPrediction: (id: string) => void
}

export function NowView({
  cards,
  predictions,
  scenarios,
  activeWork,
  selectedWorkId,
  selectedScenarioId,
  onSelectWork,
  onSelectScenario,
  onSelectPrediction,
}: NowViewProps) {
  const activePlan = activeWork?.plan_draft ?? null
  const workItems = cards.slice(0, 6)
  const directionPredictions = predictions
    .filter((prediction) => prediction.trust_status !== 'invalidated')
    .filter((prediction) => isDirectionPrediction(prediction))
    .slice(0, 6)
  const readyPredictions = predictions
    .filter((prediction) => ['ready', 'drafting'].includes(String(prediction.readiness ?? prediction.run?.readiness ?? '')))
    .filter((prediction) => prediction.trust_status !== 'invalidated')
    .filter((prediction) => !directionPredictions.some((direction) => direction.id === prediction.id))
    .slice(0, 4)
  const fallbackScenarios = scenarios.filter((scenario) => !workItems.some((card) => card.source_id === scenario.id)).slice(0, 5)
  const primaryWork = workItems[0]
  const primaryScenario = primaryWork ? null : fallbackScenarios[0]
  const secondaryWork = workItems.slice(1, 5)
  const secondaryPredictions = readyPredictions.slice(0, Math.max(0, 5 - secondaryWork.length))
  const secondaryScenarios = primaryWork
    ? fallbackScenarios.slice(0, Math.max(0, 5 - secondaryWork.length - secondaryPredictions.length))
    : fallbackScenarios.slice(1, Math.max(1, 5 - secondaryPredictions.length))

  return (
    <ViewShell eyebrow="Now" title="What matters next">
      <div className="scroll" style={viewScrollStyle}>
        {activePlan ? <PlanDraftOpportunityCard plan={activePlan} /> : null}
        {directionPredictions.length ? (
          <section style={{ ...focusSectionStyle, marginTop: activePlan ? 10 : 0, marginBottom: 12 }}>
            <SectionHeader title="Vaner Is Preparing" meta={`${directionPredictions.length} likely branches`} />
            <FocusNote>{directionSummary(directionPredictions)}</FocusNote>
            <div style={{ display: 'grid', gap: 10, marginTop: 10 }}>
              {directionPredictions.slice(0, 5).map((prediction) => (
                <PredictionOpportunityCard key={prediction.id} prediction={prediction} onSelect={onSelectPrediction} />
              ))}
            </div>
          </section>
        ) : null}
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

        <div style={{ display: 'grid', gap: 10, marginTop: activePlan ? 10 : 14 }}>
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
          {secondaryPredictions.map((prediction) => (
            <PredictionOpportunityCard key={prediction.id} prediction={prediction} onSelect={onSelectPrediction} />
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
  onSelectPrediction: (id: string) => void
}

export function BoardView({
  cards,
  predictions,
  scenarios,
  selectedWorkId,
  selectedScenarioId,
  onSelectWork,
  onSelectScenario,
  onSelectPrediction,
}: BoardViewProps) {
  const warming = predictions.filter((prediction) => !['ready', 'drafting', 'stale'].includes(String(prediction.readiness ?? prediction.run?.readiness ?? '')))
  const readyPredictions = predictions.filter((prediction) =>
    ['ready', 'drafting'].includes(String(prediction.readiness ?? prediction.run?.readiness ?? '')) && prediction.trust_status !== 'invalidated',
  )
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
            <MiniCard key={prediction.id} selected={false} title={prediction.display_label ?? prediction.title ?? prediction.label ?? prediction.prompt ?? prediction.id} meta={`${prediction.snapshot_freshness ?? prediction.readiness ?? 'warming'} · ${prediction.match_state ?? formatConfidence(prediction.confidence)}`} onClick={() => onSelectPrediction(prediction.id)} />
          ))}
        </BoardColumn>
        <BoardColumn title="Ready">
          {readyPredictions.slice(0, 8).map((prediction) => (
            <PredictionMiniCard key={prediction.id} prediction={prediction} onSelect={onSelectPrediction} />
          ))}
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

function FocusPanel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={focusPanelStyle}>
      <header className="mono" style={{ color: 'var(--fg-4)', fontSize: 10, letterSpacing: 1, marginBottom: 10 }}>
        {title}
      </header>
      <div style={{ display: 'grid', gap: 8 }}>{children}</div>
    </section>
  )
}

function FocusField({ label, value, tone = 'default' }: { label: string; value: React.ReactNode; tone?: 'default' | 'ok' | 'warn' | 'muted' }) {
  const color = tone === 'ok' ? 'var(--ok)' : tone === 'warn' ? 'var(--amber)' : tone === 'muted' ? 'var(--fg-4)' : 'var(--fg-2)'
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '96px minmax(0, 1fr)', gap: 10, alignItems: 'baseline' }}>
      <span className="mono" style={{ color: 'var(--fg-4)', fontSize: 10 }}>{label}</span>
      <span style={{ color, fontSize: 12.5, minWidth: 0, overflowWrap: 'anywhere' }}>{value}</span>
    </div>
  )
}

function FocusNote({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ color: 'var(--fg-3)', fontSize: 12, lineHeight: 1.45, borderTop: '1px solid var(--line-hair)', paddingTop: 9 }}>
      {children}
    </div>
  )
}

function SectionHeader({ title, meta }: { title: string; meta: string }) {
  return (
    <header style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 10, marginBottom: 10 }}>
      <div style={{ fontFamily: 'var(--font-display)', color: 'var(--fg-1)', fontSize: 15 }}>{title}</div>
      <div className="mono" style={{ color: 'var(--fg-4)', fontSize: 10 }}>{meta}</div>
    </header>
  )
}

function predictionTitle(prediction: PredictionSummary): string {
  return prediction.display_label ?? prediction.title ?? prediction.label ?? prediction.prompt ?? prediction.spec?.label ?? prediction.id
}

function predictionSource(prediction: PredictionSummary): string {
  return String(prediction.spec?.source ?? prediction.source_label ?? '').toLowerCase()
}

function predictionReadiness(prediction: PredictionSummary): string {
  return String(prediction.readiness ?? prediction.run?.readiness ?? 'queued')
}

function predictionTargets(prediction: PredictionSummary): string[] {
  const targets = prediction.evidence_targets ?? prediction.watched_sources ?? prediction.spec?.structured?.evidence_targets ?? []
  return targets.filter((target): target is string => typeof target === 'string' && target.length > 0)
}

function isDirectionPrediction(prediction: PredictionSummary): boolean {
  const source = predictionSource(prediction)
  const label = String(prediction.source_label ?? '').toLowerCase()
  const readiness = predictionReadiness(prediction)
  return readiness !== 'stale' && (source.includes('horizon') || label.includes('possible next work'))
}

function predictionReason(prediction: PredictionSummary): string {
  const matchReason = prediction.match_reason ?? ''
  if (matchReason && !matchReason.toLowerCase().startsWith('no current-turn')) {
    return matchReason
  }
  return prediction.ui_summary ?? prediction.spec?.description ?? 'Preparing this as likely next work.'
}

function predictionTargetLabel(prediction: PredictionSummary): string {
  const targets = predictionTargets(prediction)
  if (!targets.length) {
    return prediction.recommended_action === 'adopt' ? 'ready to use' : 'workspace'
  }
  if (targets.length === 1) {
    return compactPath(targets[0])
  }
  return `${targets.slice(0, 2).map(compactPath).join(', ')} +${targets.length - 2}`
}

function predictionActionLabel(prediction: PredictionSummary): string {
  const readiness = predictionReadiness(prediction)
  if (prediction.recommended_action === 'adopt') return 'Ready'
  if (readiness === 'ready' || readiness === 'drafting') return 'Ready'
  return 'Preparing'
}

function directionSummary(predictions: PredictionSummary[]): string {
  const labels = predictions.slice(0, 3).map(predictionTitle)
  if (!labels.length) {
    return 'No likely next work is visible yet.'
  }
  return `Preparing likely next work: ${labels.join('; ')}.`
}

function compactPath(path: string): string {
  const parts = path.split('/').filter(Boolean)
  return parts.slice(-2).join('/') || path
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

function PredictionOpportunityCard({ prediction, onSelect }: { prediction: PredictionSummary; onSelect?: (id: string) => void }) {
  return (
    <OpportunityCard
      selected={false}
      title={predictionTitle(prediction)}
      type={prediction.source_label ?? 'Prepared context'}
      confidence={formatConfidence(prediction.confidence)}
      freshness={prediction.snapshot_freshness ?? prediction.freshness ?? 'recent'}
      evidence={prediction.readiness_label ?? String(prediction.readiness ?? prediction.run?.readiness ?? 'ready')}
      target={predictionTargetLabel(prediction)}
      reason={predictionReason(prediction)}
      action={predictionActionLabel(prediction)}
      onClick={() => onSelect?.(prediction.id)}
    />
  )
}

function PlanDraftOpportunityCard({ plan }: { plan: PlanDraftSummary }) {
  return (
    <div style={{ ...opportunityStyle(true, true), cursor: 'default', marginBottom: 12 }}>
      <span style={{ display: 'flex', alignItems: 'start', justifyContent: 'space-between', gap: 12 }}>
        <span style={{ minWidth: 0 }}>
          <span className="mono" style={{ display: 'block', color: 'var(--accent)', fontSize: 10, letterSpacing: 1 }}>
            ACTIVE DRAFT PLAN
          </span>
          <span style={{ display: 'block', fontFamily: 'var(--font-display)', fontSize: 20, color: 'var(--fg-1)', lineHeight: 1.2, marginTop: 5 }}>
            {plan.title}
          </span>
          <span style={{ display: 'block', color: 'var(--fg-3)', fontSize: 12.5, marginTop: 7, lineHeight: 1.45 }}>{plan.summary}</span>
        </span>
        <span style={badgeStyle}>{plan.status}</span>
      </span>
      {plan.tasks.length ? (
        <div style={{ display: 'grid', gap: 5, marginTop: 10 }}>
          {plan.tasks.slice(0, 5).map((task, index) => (
            <div key={`${plan.id}-${index}`} style={{ display: 'grid', gridTemplateColumns: '18px minmax(0, 1fr)', gap: 7, color: 'var(--fg-2)', fontSize: 12, lineHeight: 1.35 }}>
              <span className="mono" style={{ color: 'var(--fg-4)' }}>{index + 1}</span>
              <span>{task}</span>
            </div>
          ))}
        </div>
      ) : null}
      <span className="mono" style={metaRowStyle}>
        {plan.source_client} · shadow prep area · {plan.prep_dir || 'runtime prep'}
      </span>
    </div>
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

function PredictionMiniCard({ prediction, onSelect }: { prediction: PredictionSummary; onSelect?: (id: string) => void }) {
  return (
    <MiniCard
      selected={false}
      title={predictionTitle(prediction)}
      meta={`${prediction.source_label ?? 'prepared context'} · ${prediction.readiness_label ?? predictionReadiness(prediction)} · ${predictionTargetLabel(prediction)}`}
      onClick={() => onSelect?.(prediction.id)}
    />
  )
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

const focusGridStyle: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
  gap: 12,
  alignItems: 'start',
}

const focusPanelStyle: React.CSSProperties = {
  background: 'var(--bg-1)',
  border: '1px solid var(--line-1)',
  borderRadius: 'var(--r-2)',
  padding: 14,
  minWidth: 0,
}

const focusSectionStyle: React.CSSProperties = {
  marginTop: 14,
  background: 'var(--bg-1)',
  border: '1px solid var(--line-1)',
  borderRadius: 'var(--r-2)',
  padding: 14,
}

const focusBannerStyle: React.CSSProperties = {
  border: '1px solid var(--line-1)',
  borderRadius: 'var(--r-1)',
  background: 'var(--bg-1)',
  color: 'var(--fg-3)',
  padding: '10px 12px',
  fontSize: 12,
  marginBottom: 12,
}

const focusJobStyle: React.CSSProperties = {
  border: '1px solid var(--line-hair)',
  borderRadius: 'var(--r-1)',
  background: 'var(--bg-inset)',
  padding: 10,
  minWidth: 0,
}

const signalStyle: React.CSSProperties = {
  border: '1px solid var(--line-hair)',
  borderRadius: 'var(--r-1)',
  background: 'var(--bg-inset)',
  padding: 11,
  minWidth: 0,
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
