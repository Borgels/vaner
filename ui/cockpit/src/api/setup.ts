// SPDX-License-Identifier: Apache-2.0
// 0.8.6 WS10 — Cockpit fetch helpers for the (forthcoming) WS8 setup
// HTTP surface: /setup/status, /policy/current, /hardware/profile, and
// POST /setup/apply.
//
// WS8 has not yet shipped these endpoints. Until it does, the GET helpers
// gracefully resolve to `null` on a 404 so the BundleSummaryCard /
// HardwareProfilePanel can render a friendly "not yet available" state
// instead of crashing the cockpit. POST /setup/apply is a mutation —
// callers should disable the action button if the matching status fetch
// already returned null, and the helper itself raises on 404.
//
// API_BASE follows the same convention as the rest of the cockpit —
// override via VITE_VANER_DAEMON_URL at build time. See deepRun.ts for
// the canonical pattern.

import type {
  AppliedPolicy,
  HardwareProfile,
  SetupAnswers,
  SetupStatus,
} from '../types/setup'

const API_BASE: string =
  (typeof import.meta !== 'undefined' &&
    (import.meta as { env?: Record<string, string> }).env?.VITE_VANER_DAEMON_URL) ||
  'http://127.0.0.1:8473'

class SetupFetchError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'SetupFetchError'
  }
}

async function _getOrNull<T>(path: string): Promise<T | null> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers: { accept: 'application/json' },
    })
  } catch (err) {
    // Network error: treat as "not yet available" so the panel renders
    // its friendly fallback rather than crashing. The daemon may simply
    // not be running yet on the cockpit-only dev path.
    void err
    return null
  }
  if (response.status === 404) {
    return null
  }
  if (!response.ok) {
    const text = await response.text().catch(() => '')
    throw new SetupFetchError(
      response.status,
      `${response.status} ${response.statusText}: ${text}`,
    )
  }
  return (await response.json()) as T
}

/** Fetch the wizard completion state. Returns `null` if WS8 is not yet shipped. */
export async function fetchSetupStatus(): Promise<SetupStatus | null> {
  return _getOrNull<SetupStatus>('/setup/status')
}

/** Fetch the currently applied policy. Returns `null` if WS8 is not yet shipped. */
export async function fetchPolicyCurrent(): Promise<AppliedPolicy | null> {
  return _getOrNull<AppliedPolicy>('/policy/current')
}

/** Fetch the hardware probe. Returns `null` if WS8 is not yet shipped. */
export async function fetchHardwareProfile(): Promise<HardwareProfile | null> {
  return _getOrNull<HardwareProfile>('/hardware/profile')
}

/**
 * Apply a SetupAnswers payload. Mutation — raises on 404 (caller should
 * have disabled the submit button if status fetch already returned null).
 *
 * `bundleId` lets the user pin a specific bundle; when omitted the daemon
 * falls back to running select_policy_bundle() on the answers.
 */
export async function postSetupApply(
  answers: SetupAnswers,
  bundleId?: string,
): Promise<AppliedPolicy> {
  const body: Record<string, unknown> = { answers }
  if (bundleId) {
    body.bundle_id = bundleId
  }
  const response = await fetch(`${API_BASE}/setup/apply`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    const text = await response.text().catch(() => '')
    throw new SetupFetchError(
      response.status,
      `${response.status} ${response.statusText}: ${text}`,
    )
  }
  return (await response.json()) as AppliedPolicy
}

export { SetupFetchError }
