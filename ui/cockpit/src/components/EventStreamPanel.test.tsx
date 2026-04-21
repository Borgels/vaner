import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { EventStreamPanel } from './EventStreamPanel'
import type { PipelineEvent } from '../api/usePipelineEvents'

function event(partial: Partial<PipelineEvent> & { id: string; kind: string; stage: PipelineEvent['stage'] }): PipelineEvent {
  return {
    id: partial.id,
    t: '00:00.00',
    tag: partial.kind.split('.')[0],
    color: 'var(--fg-3)',
    msg: partial.msg ?? `${partial.stage}:${partial.kind}`,
    scn: partial.scn ?? null,
    stage: partial.stage,
    kind: partial.kind,
    ts: partial.ts ?? 0,
    path: partial.path ?? null,
    cycleId: partial.cycleId ?? null,
    payload: partial.payload ?? {},
  }
}

describe('EventStreamPanel', () => {
  it('renders events coloured by stage and shows a tailing indicator', () => {
    render(
      <EventStreamPanel
        title="EVENT STREAM"
        subtitle="Live"
        events={[
          event({ id: 'a', kind: 'llm.request', stage: 'model' }),
          event({ id: 'b', kind: 'artefact.upsert', stage: 'artefacts' }),
        ]}
        onSelect={() => undefined}
        live
      />,
    )

    expect(screen.getByText('tailing')).toBeInTheDocument()
    expect(screen.getByText(/model:llm.request/)).toBeInTheDocument()
    expect(screen.getByText(/artefacts:artefact.upsert/)).toBeInTheDocument()
  })

  it('filters events by stage when a chip is toggled', () => {
    render(
      <EventStreamPanel
        title="EVENT STREAM"
        subtitle="Live"
        events={[
          event({ id: 'a', kind: 'llm.request', stage: 'model' }),
          event({ id: 'b', kind: 'artefact.upsert', stage: 'artefacts' }),
        ]}
        onSelect={() => undefined}
        live
      />,
    )

    const modelChip = screen.getByRole('button', { name: /^model$/i })
    fireEvent.click(modelChip)

    expect(screen.queryByText(/artefacts:artefact.upsert/)).toBeNull()
    expect(screen.getByText(/model:llm.request/)).toBeInTheDocument()
  })

  it('invokes onSelect with the scenario id when an event row is clicked', () => {
    const onSelect = vi.fn()
    render(
      <EventStreamPanel
        title="EVENT STREAM"
        subtitle="Live"
        events={[
          event({
            id: 'a',
            kind: 'artefact.upsert',
            stage: 'artefacts',
            scn: 'scn_123',
            msg: 'scn_123 upsert',
          }),
        ]}
        onSelect={onSelect}
        live
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /scn_123/i }))
    expect(onSelect).toHaveBeenCalledWith('scn_123')
  })

  it('shows a spinner indicator when pendingLlm > 0', () => {
    const { container } = render(
      <EventStreamPanel
        title="EVENT STREAM"
        subtitle="Live"
        events={[]}
        onSelect={() => undefined}
        live
        pendingLlm={2}
      />,
    )
    expect(screen.getByText(/llm×2/)).toBeInTheDocument()
    // Spinner is an unlabelled span; just assert the waiting marker didn't
    // replace it.
    expect(container.textContent).not.toContain('waiting')
  })
})
