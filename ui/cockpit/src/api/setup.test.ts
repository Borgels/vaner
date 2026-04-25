import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  fetchHardwareProfile,
  fetchPolicyCurrent,
  fetchSetupStatus,
  postSetupApply,
  SetupFetchError,
} from './setup'
import type { SetupAnswers } from '../types/setup'

const ORIGINAL_FETCH = globalThis.fetch

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('setup API helpers', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn()
  })

  afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH
    vi.restoreAllMocks()
  })

  it('fetchSetupStatus returns null on 404 (WS8 not yet shipped)', async () => {
    ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response('not found', { status: 404 }),
    )
    const result = await fetchSetupStatus()
    expect(result).toBeNull()
  })

  it('fetchSetupStatus parses a 200 JSON body', async () => {
    ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse({ completed: true, selected_bundle_id: 'hybrid_balanced' }),
    )
    const result = await fetchSetupStatus()
    expect(result).toEqual({
      completed: true,
      selected_bundle_id: 'hybrid_balanced',
    })
  })

  it('fetchSetupStatus returns null on network error rather than throwing', async () => {
    ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new TypeError('NetworkError when attempting to fetch resource.'),
    )
    const result = await fetchSetupStatus()
    expect(result).toBeNull()
  })

  it('fetchPolicyCurrent returns null on 404', async () => {
    ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response('', { status: 404 }),
    )
    const result = await fetchPolicyCurrent()
    expect(result).toBeNull()
  })

  it('fetchPolicyCurrent throws SetupFetchError on non-404 errors', async () => {
    ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response('boom', { status: 500 }),
    )
    await expect(fetchPolicyCurrent()).rejects.toBeInstanceOf(SetupFetchError)
  })

  it('fetchHardwareProfile returns null on 404', async () => {
    ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response('', { status: 404 }),
    )
    const result = await fetchHardwareProfile()
    expect(result).toBeNull()
  })

  it('postSetupApply raises on 404 (mutation)', async () => {
    ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response('not found', { status: 404 }),
    )
    const answers: SetupAnswers = {
      work_styles: ['mixed'],
      priority: 'balanced',
      compute_posture: 'balanced',
      cloud_posture: 'hybrid_when_worth_it',
      background_posture: 'normal',
    }
    await expect(postSetupApply(answers)).rejects.toBeInstanceOf(SetupFetchError)
  })

  it('postSetupApply sends the bundle_id when provided', async () => {
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        bundle_id: 'cost_saver',
        overrides_applied: ['BackendConfig.prefer_local: true'],
      }),
    )
    const answers: SetupAnswers = {
      work_styles: ['coding'],
      priority: 'cost',
      compute_posture: 'light',
      cloud_posture: 'local_only',
      background_posture: 'minimal',
    }
    const result = await postSetupApply(answers, 'cost_saver')
    expect(result.bundle_id).toBe('cost_saver')
    const call = fetchMock.mock.calls[0]
    const init = call[1] as RequestInit
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({
      answers,
      bundle_id: 'cost_saver',
    })
  })
})
