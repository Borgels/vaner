import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ActivePredictionsPanel, type PredictionRow } from './ActivePredictionsPanel'

function makeRow(overrides: Partial<PredictionRow> = {}): PredictionRow {
  return {
    id: overrides.id ?? 'pred-1',
    spec: {
      label: overrides.spec?.label ?? 'Write the next test',
      description: overrides.spec?.description ?? 'Some description',
      source: overrides.spec?.source ?? 'arc',
      anchor: overrides.spec?.anchor ?? 'anchor',
      confidence: overrides.spec?.confidence ?? 0.7,
      hypothesis_type: overrides.spec?.hypothesis_type ?? 'likely_next',
      specificity: overrides.spec?.specificity ?? 'concrete',
    },
    run: {
      weight: overrides.run?.weight ?? 1.0,
      token_budget: overrides.run?.token_budget ?? 400,
      tokens_used: overrides.run?.tokens_used ?? 100,
      model_calls: overrides.run?.model_calls ?? 2,
      scenarios_spawned: overrides.run?.scenarios_spawned ?? 1,
      scenarios_complete: overrides.run?.scenarios_complete ?? 0,
      readiness: overrides.run?.readiness ?? 'ready',
    },
    artifacts: {
      evidence_score: overrides.artifacts?.evidence_score ?? 0.5,
      has_draft: overrides.artifacts?.has_draft ?? true,
      has_briefing: overrides.artifacts?.has_briefing ?? true,
    },
  }
}

function makeFetcher(rows: PredictionRow[]) {
  return vi.fn(async () =>
    new Response(JSON.stringify({ predictions: rows }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
}

describe('ActivePredictionsPanel', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('renders the loading state initially', () => {
    const fetcher = vi.fn(() => new Promise<Response>(() => {})) // never resolves
    render(<ActivePredictionsPanel fetcher={fetcher as unknown as typeof fetch} />)
    expect(screen.getByText(/Loading/i)).toBeInTheDocument()
  })

  it('renders a row with the likely_next prefix', async () => {
    const fetcher = makeFetcher([makeRow()])
    render(<ActivePredictionsPanel fetcher={fetcher as unknown as typeof fetch} />)
    await waitFor(() => expect(fetcher).toHaveBeenCalled())
    expect(await screen.findByText(/Next step:/)).toBeInTheDocument()
    expect(screen.getByText(/Write the next test/)).toBeInTheDocument()
  })

  it('shows the readiness state on each row', async () => {
    const fetcher = makeFetcher([makeRow({ run: { readiness: 'drafting' } as any })])
    render(<ActivePredictionsPanel fetcher={fetcher as unknown as typeof fetch} />)
    await waitFor(() => expect(fetcher).toHaveBeenCalled())
    expect(await screen.findByText(/drafting/)).toBeInTheDocument()
  })

  it('renders the empty-state message when no predictions are active', async () => {
    const fetcher = makeFetcher([])
    render(<ActivePredictionsPanel fetcher={fetcher as unknown as typeof fetch} />)
    await waitFor(() => expect(fetcher).toHaveBeenCalled())
    expect(await screen.findByText(/No active predictions yet/i)).toBeInTheDocument()
  })

  it('renders an error message when the endpoint fails', async () => {
    const fetcher = vi.fn(async () =>
      new Response('oops', { status: 500 }),
    )
    render(<ActivePredictionsPanel fetcher={fetcher as unknown as typeof fetch} />)
    await waitFor(() => expect(fetcher).toHaveBeenCalled())
    expect(await screen.findByRole('alert')).toHaveTextContent(/Error/)
  })

  it('calls onAdopt with the row id when a ready row is clicked', async () => {
    const fetcher = makeFetcher([makeRow({ run: { readiness: 'ready' } as any })])
    const onAdopt = vi.fn()
    render(
      <ActivePredictionsPanel
        fetcher={fetcher as unknown as typeof fetch}
        onAdopt={onAdopt}
      />,
    )
    const button = await screen.findByRole('button', { name: /Adopt/ })
    fireEvent.click(button)
    expect(onAdopt).toHaveBeenCalledWith('pred-1')
  })

  it('disables click for non-ready/non-drafting rows', async () => {
    const fetcher = makeFetcher([makeRow({ run: { readiness: 'grounding' } as any })])
    const onAdopt = vi.fn()
    render(
      <ActivePredictionsPanel
        fetcher={fetcher as unknown as typeof fetch}
        onAdopt={onAdopt}
      />,
    )
    const button = await screen.findByRole('button', { name: /Adopt/ })
    expect(button).toBeDisabled()
    fireEvent.click(button)
    expect(onAdopt).not.toHaveBeenCalled()
  })

  it('differentiates label prefix by hypothesis_type', async () => {
    const fetcher = makeFetcher([
      makeRow({
        id: 'p-branch',
        spec: {
          label: 'Refactor parser',
          description: '',
          source: 'arc',
          anchor: 'a',
          confidence: 0.45,
          hypothesis_type: 'possible_branch',
          specificity: 'concrete',
        } as any,
      }),
    ])
    render(<ActivePredictionsPanel fetcher={fetcher as unknown as typeof fetch} />)
    await waitFor(() => expect(fetcher).toHaveBeenCalled())
    expect(await screen.findByText(/Vaner is exploring:/)).toBeInTheDocument()
  })
})
