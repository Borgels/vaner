import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { PipelineCanvas } from './PipelineCanvas'
import type {
  ArtefactRow,
  CycleState,
  DecisionRow,
  ModelState,
  PipelineEvent,
  SignalRow,
  TargetRow,
} from '../api/usePipelineEvents'

function model(overrides: Partial<ModelState> = {}): ModelState {
  return {
    pending: overrides.pending ?? new Map(),
    lastLatencyMs: overrides.lastLatencyMs ?? null,
    lastModel: overrides.lastModel ?? null,
    recentLatencies: overrides.recentLatencies ?? [],
    totalRequests: overrides.totalRequests ?? 0,
    totalErrors: overrides.totalErrors ?? 0,
  }
}

function cycle(overrides: Partial<CycleState> = {}): CycleState {
  return {
    current: overrides.current ?? null,
    lastFinished: overrides.lastFinished ?? null,
    totalCycles: overrides.totalCycles ?? 0,
    artefactsWritten: overrides.artefactsWritten ?? 0,
  }
}

const SIGNALS: SignalRow[] = [
  { cycleId: 'c1', ts: 0, fsScan: 5, gitChanged: 2, msg: 'scan' },
]
const TARGETS: TargetRow[] = [
  { cycleId: 'c1', ts: 0, count: 4, paths: ['src/api.py', 'src/util.py'] },
]
const ARTEFACTS: ArtefactRow[] = [
  { id: 'ev1', ts: 0, kind: 'file_summary', path: 'src/api.py', cycleId: 'c1' },
]
const DECISIONS: DecisionRow[] = [
  { id: 'ev2', ts: 0, decisionId: 'dec_1', selectionCount: 3, cacheTier: 'warm' },
]
const EVENTS: PipelineEvent[] = []

describe('PipelineCanvas', () => {
  it('renders all six pipeline lanes', () => {
    render(
      <PipelineCanvas
        scenarios={[]}
        selectedId={null}
        onSelect={() => undefined}
        activePulses={new Set()}
        pinnedIds={new Set()}
        signals={SIGNALS}
        targets={TARGETS}
        artefacts={ARTEFACTS}
        decisions={DECISIONS}
        model={model()}
        cycle={cycle()}
        events={EVENTS}
      />,
    )

    for (const label of ['SIGNALS', 'TARGETS', 'MODEL', 'ARTEFACTS', 'SCENARIOS', 'DECISIONS']) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
  })

  it('shows the latest target path in the targets lane', () => {
    render(
      <PipelineCanvas
        scenarios={[]}
        selectedId={null}
        onSelect={() => undefined}
        activePulses={new Set()}
        pinnedIds={new Set()}
        signals={SIGNALS}
        targets={TARGETS}
        artefacts={ARTEFACTS}
        decisions={DECISIONS}
        model={model()}
        cycle={cycle()}
        events={EVENTS}
      />,
    )

    expect(screen.getAllByText('src/api.py').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/4 planned/)).toBeInTheDocument()
  })

  it('renders the empty state when no scenarios exist', () => {
    render(
      <PipelineCanvas
        scenarios={[]}
        selectedId={null}
        onSelect={() => undefined}
        activePulses={new Set()}
        pinnedIds={new Set()}
        signals={[]}
        targets={[]}
        artefacts={[]}
        decisions={[]}
        model={model()}
        cycle={cycle()}
        events={EVENTS}
      />,
    )

    expect(screen.getByText(/Waiting for the daemon/)).toBeInTheDocument()
  })

  it('flashes the model lane with a spinner while LLM requests are pending', () => {
    const pending = new Map([
      ['ev-1', { path: 'src/a.py', model: 'qwen', startedAt: Date.now() / 1000 }],
    ])
    render(
      <PipelineCanvas
        scenarios={[]}
        selectedId={null}
        onSelect={() => undefined}
        activePulses={new Set()}
        pinnedIds={new Set()}
        signals={SIGNALS}
        targets={TARGETS}
        artefacts={ARTEFACTS}
        decisions={DECISIONS}
        model={model({ pending, lastModel: 'qwen' })}
        cycle={cycle()}
        events={EVENTS}
      />,
    )

    expect(screen.getByText(/1 in flight/)).toBeInTheDocument()
  })
})
