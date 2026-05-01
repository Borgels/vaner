import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { GoalsPanel } from './GoalsPanel'

const ORIGINAL_FETCH = global.fetch

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('GoalsPanel', () => {
  afterEach(() => {
    global.fetch = ORIGINAL_FETCH
    vi.restoreAllMocks()
  })

  it('renders the empty-state when no goals are declared', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/goals')) return jsonResponse({ goals: [] })
      if (url.startsWith('/artefacts')) return jsonResponse({ artefacts: [] })
      return new Response('not found', { status: 404 })
    })
    global.fetch = fetchMock as unknown as typeof fetch

    render(<GoalsPanel refreshIntervalMs={60_000} />)

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(
      await screen.findByText(/No workspace goals declared/i),
    ).toBeInTheDocument()
  })

  it('renders a goal row with status and reconciliation hint', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/goals')) {
        return jsonResponse({
          goals: [
            {
              id: 'goal-1',
              title: 'Clean up the auth middleware',
              status: 'active',
              description: 'Remove the legacy session-token paths',
              pc_reconciliation_state: 'reconciled',
              pc_freshness: 0.8,
            },
          ],
        })
      }
      if (url.startsWith('/artefacts')) return jsonResponse({ artefacts: [] })
      return new Response('not found', { status: 404 })
    })
    global.fetch = fetchMock as unknown as typeof fetch

    render(<GoalsPanel refreshIntervalMs={60_000} />)

    expect(
      await screen.findByText('Clean up the auth middleware'),
    ).toBeInTheDocument()
    expect(screen.getByText('active')).toBeInTheDocument()
    // Header shows count summary.
    expect(screen.getByText(/1 active/)).toBeInTheDocument()
  })
})
