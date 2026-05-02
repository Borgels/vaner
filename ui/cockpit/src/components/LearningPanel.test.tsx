import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { LearningPanel } from './LearningPanel'

const ORIGINAL_FETCH = global.fetch

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('LearningPanel', () => {
  afterEach(() => {
    global.fetch = ORIGINAL_FETCH
    vi.restoreAllMocks()
  })

  it('renders the empty-state when no feedback or learning is recorded', async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ feedback_events: [], learning_state: {} }),
    )
    global.fetch = fetchMock as unknown as typeof fetch

    render(<LearningPanel refreshIntervalMs={60_000} />)

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(
      await screen.findByText(/No feedback recorded yet/i),
    ).toBeInTheDocument()
  })

  it('renders a recent useful feedback event with its scenario id', async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({
        feedback_events: [
          {
            id: 'feedback-1',
            timestamp: Date.now() / 1000 - 30,
            scenario_id: 'scn-42',
            metadata_json: '{"feedback_kind": "useful"}',
          },
        ],
        learning_state: {
          skill_weights: { tests: 0.6, refactor: 0.3 },
        },
      }),
    )
    global.fetch = fetchMock as unknown as typeof fetch

    render(<LearningPanel refreshIntervalMs={60_000} />)

    expect(await screen.findByText('useful')).toBeInTheDocument()
    expect(screen.getByText('scn-42')).toBeInTheDocument()
    expect(screen.getByText(/skill_weights/)).toBeInTheDocument()
  })
})
