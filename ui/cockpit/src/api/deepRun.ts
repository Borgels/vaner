// SPDX-License-Identifier: Apache-2.0
// 0.8.3 WS4 — Cockpit fetch helpers for the daemon's /deep-run/*
// endpoints.  Mirrors the schema shipped by src/vaner/daemon/http.py.
//
// `API_BASE` follows the same convention as the rest of the cockpit
// (the daemon listens on 127.0.0.1:8473 by default; override via
// `VITE_VANER_DAEMON_URL` at build time).  When the daemon is not
// reachable, every helper rejects with a Response-shaped Error so
// callers can surface a clear "daemon offline" state rather than
// silently absorbing failures.

import type {
  DeepRunSession,
  DeepRunSummary,
  StartDeepRunRequest,
  StopDeepRunRequest,
} from '../types/deepRun'

const API_BASE: string =
  (typeof import.meta !== 'undefined' &&
    (import.meta as { env?: Record<string, string> }).env?.VITE_VANER_DAEMON_URL) ||
  'http://127.0.0.1:8473'

async function _request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'content-type': 'application/json', ...(init?.headers || {}) },
    ...init,
  })
  if (!response.ok) {
    const text = await response.text().catch(() => '')
    throw new Error(`${response.status} ${response.statusText}: ${text}`)
  }
  return (await response.json()) as T
}

export async function startDeepRun(
  body: StartDeepRunRequest,
): Promise<DeepRunSession> {
  return _request<DeepRunSession>('/deep-run/start', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function stopDeepRun(
  body: StopDeepRunRequest = {},
): Promise<{ summary: DeepRunSummary | null }> {
  return _request<{ summary: DeepRunSummary | null }>('/deep-run/stop', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function getDeepRunStatus(): Promise<{ session: DeepRunSession | null }> {
  return _request<{ session: DeepRunSession | null }>('/deep-run/status')
}

export async function listDeepRunSessions(
  limit: number = 20,
): Promise<{ sessions: DeepRunSession[] }> {
  return _request<{ sessions: DeepRunSession[] }>(
    `/deep-run/sessions?limit=${encodeURIComponent(limit)}`,
  )
}

export async function getDeepRunSession(
  sessionId: string,
): Promise<DeepRunSession> {
  return _request<DeepRunSession>(
    `/deep-run/sessions/${encodeURIComponent(sessionId)}`,
  )
}
