import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { PreparedWorkCard } from '../types'
import { PreparedWorkPanel } from './PreparedWorkPanel'

function makeCard(overrides: Partial<PreparedWorkCard> = {}): PreparedWorkCard {
  return {
    id: overrides.id ?? 'work_product:wp-1',
    source_id: overrides.source_id ?? 'wp-1',
    source_type: overrides.source_type ?? 'work_product',
    kind: overrides.kind ?? 'diff',
    title: overrides.title ?? 'Prepared parser fix',
    summary: overrides.summary ?? 'A one-file patch is ready.',
    badge: overrides.badge ?? 'Diff',
    confidence_label: overrides.confidence_label ?? 'High',
    freshness_label: overrides.freshness_label ?? 'Fresh',
    target_label: overrides.target_label ?? 'src/parser.py',
    evidence_count: overrides.evidence_count ?? 2,
    created_at: overrides.created_at ?? 100,
    updated_at: overrides.updated_at ?? 100,
    primary_action: overrides.primary_action ?? {
      kind: 'export',
      label: 'Export',
      tool: 'vaner.work_products.export',
      endpoint: '/work-products/wp-1/export',
      arguments: { work_product_id: 'wp-1' },
    },
    secondary_actions: overrides.secondary_actions ?? [
      {
        kind: 'inspect',
        label: 'Inspect',
        tool: 'vaner.work_products.inspect',
        endpoint: '/work-products/wp-1',
        arguments: { work_product_id: 'wp-1' },
      },
      {
        kind: 'dismiss',
        label: 'Dismiss',
        tool: 'vaner.work_products.dismiss',
        endpoint: '/work-products/wp-1/dismiss',
        arguments: { work_product_id: 'wp-1' },
      },
    ],
    diagnostic_refs: overrides.diagnostic_refs ?? [],
    sensitivity_class: overrides.sensitivity_class,
    fresh_precheck_required: overrides.fresh_precheck_required,
    external_input_count: overrides.external_input_count,
    prohibited_actions: overrides.prohibited_actions,
    action_note: overrides.action_note,
  }
}

describe('PreparedWorkPanel', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders prepared work cards from the unified endpoint', async () => {
    const fetcher = vi.fn(async () =>
      new Response(JSON.stringify({ prepared_work: [makeCard()] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    render(<PreparedWorkPanel fetcher={fetcher as unknown as typeof fetch} />)

    expect(await screen.findByText('Prepared parser fix')).toBeInTheDocument()
    expect(screen.getByText('Diff')).toBeInTheDocument()
    expect(screen.getByText('src/parser.py')).toBeInTheDocument()
    expect(fetcher).toHaveBeenCalledWith('/prepared-work?surface=cockpit&limit=6')
  })

  it('does not render lifecycle/adoptability internals as user-facing text', async () => {
    const fetcher = vi.fn(async () =>
      new Response(JSON.stringify({ prepared_work: [makeCard()] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    render(<PreparedWorkPanel fetcher={fetcher as unknown as typeof fetch} />)

    await screen.findByText('Prepared parser fix')
    expect(screen.queryByText(/adoptability/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/self_eval/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/lifecycle/i)).not.toBeInTheDocument()
  })

  it('uses server-provided actions and shows returned export detail', async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes('/prepared-work')) {
        return new Response(JSON.stringify({ prepared_work: [makeCard()] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response(JSON.stringify({ body: '```diff\\n+fixed\\n```' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })

    render(<PreparedWorkPanel fetcher={fetcher as unknown as typeof fetch} />)

    const button = await screen.findByRole('button', { name: 'Export' })
    fireEvent.click(button)

    await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/work-products/wp-1/export', expect.objectContaining({ method: 'POST' })))
    expect(await screen.findByLabelText('Prepared work detail')).toHaveTextContent('+fixed')
  })

  it('opens inspect results inline on the selected prepared work card', async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes('/prepared-work')) {
        return new Response(JSON.stringify({ prepared_work: [makeCard()] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response(JSON.stringify({
        title: 'Prepared parser fix',
        source_id: 'wp-1',
        source_type: 'work_product',
        kind: 'diff',
        why_prepared: 'Ready because recent parser edits are likely to need follow-up.',
        body: 'Inspect the parser error branch before using this.',
        evidence_refs: [],
        events: [],
        self_eval: {
          evidence_coverage: 0.76,
          groundedness: 0.84,
          contradiction_risk: 0.12,
        },
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })

    render(<PreparedWorkPanel fetcher={fetcher as unknown as typeof fetch} />)

    const button = await screen.findByRole('button', { name: 'Inspect' })
    fireEvent.click(button)

    expect(await screen.findByTestId('prepared-work-inspection')).toHaveTextContent(
      'Inspect the parser error branch before using this.',
    )
    expect(screen.getByText('Ready because recent parser edits are likely to need follow-up.')).toBeInTheDocument()
    expect(fetcher).toHaveBeenCalledWith('/work-products/wp-1', expect.objectContaining({ method: 'GET' }))
  })

  it('renders an empty state when nothing is ready', async () => {
    const fetcher = vi.fn(async () =>
      new Response(JSON.stringify({ prepared_work: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    render(<PreparedWorkPanel fetcher={fetcher as unknown as typeof fetch} />)

    expect(await screen.findByText(/No prepared work is ready/i)).toBeInTheDocument()
  })

  it('renders finance prepared work as trading prep', async () => {
    const fetcher = vi.fn(async () =>
      new Response(JSON.stringify({
        prepared_work: [
          makeCard({
            kind: 'finance',
            title: 'Trading strategy prep',
            summary: 'Uses fresh market, account, options, and news inputs.',
            badge: 'Trading',
            sensitivity_class: 'position_specific',
            fresh_precheck_required: true,
            external_input_count: 5,
            primary_action: {
              kind: 'inspect',
              label: 'Inspect',
              tool: 'vaner.work_products.inspect',
              endpoint: '/work-products/wp-finance/inspect',
              arguments: { work_product_id: 'wp-finance' },
            },
          }),
        ],
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    render(<PreparedWorkPanel fetcher={fetcher as unknown as typeof fetch} />)

    expect(await screen.findByText('Trading strategy prep')).toBeInTheDocument()
    expect(screen.getByText('trading prep')).toBeInTheDocument()
    expect(screen.getByText('position_specific')).toBeInTheDocument()
    expect(screen.getByText('fresh precheck')).toBeInTheDocument()
    expect(screen.getByText('5 external')).toBeInTheDocument()
  })
})
