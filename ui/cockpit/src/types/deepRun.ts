// SPDX-License-Identifier: Apache-2.0
// 0.8.3 WS4 — TypeScript mirrors of the Python DeepRunSession /
// DeepRunSummary schema.  Kept here so cockpit code can import strict
// types without hand-rolling them at every call site.  The Python
// canonical definitions live in src/vaner/intent/deep_run.py and the
// daemon HTTP surface in src/vaner/daemon/http.py serialises rows in
// this exact shape.  Keep the field names byte-for-byte aligned with
// the server-side _session_to_dict / _summary_to_dict helpers in
// src/vaner/cli/commands/deep_run.py — those are the on-the-wire schema.

export type DeepRunPreset = 'conservative' | 'balanced' | 'aggressive'
export type DeepRunFocus = 'active_goals' | 'current_workspace' | 'all_recent'
export type DeepRunHorizonBias =
  | 'likely_next'
  | 'long_horizon'
  | 'finish_partials'
  | 'balanced'
export type DeepRunLocality = 'local_only' | 'local_preferred' | 'allow_cloud'
export type DeepRunStatus = 'active' | 'paused' | 'ended' | 'killed'
export type DeepRunPauseReason =
  | 'battery'
  | 'thermal'
  | 'user_input_observed'
  | 'engine_error_rate'
  | 'cost_cap_exceeded'
  | 'user_requested'

export interface DeepRunSession {
  id: string
  status: DeepRunStatus
  preset: DeepRunPreset
  focus: DeepRunFocus
  horizon_bias: DeepRunHorizonBias
  locality: DeepRunLocality
  cost_cap_usd: number
  spend_usd: number
  workspace_root: string
  started_at: number
  ends_at: number
  ended_at: number | null
  cycles_run: number
  // Honest 4-counter discipline (spec §9.2 / §14.1): surfaces must show
  // all four maturation outcome counts, never just `kept`.
  matured_kept: number
  matured_discarded: number
  matured_rolled_back: number
  matured_failed: number
  promoted_count: number
  pause_reasons: DeepRunPauseReason[]
  cancelled_reason: string | null
  metadata: Record<string, string>
}

export interface DeepRunSummary {
  session_id: string
  started_at: number
  ended_at: number
  preset: DeepRunPreset
  cycles_run: number
  matured_kept: number
  matured_discarded: number
  matured_rolled_back: number
  matured_failed: number
  promoted_count: number
  spend_usd: number
  pause_reasons: DeepRunPauseReason[]
  cancelled_reason: string | null
  final_status: DeepRunStatus
}

export interface StartDeepRunRequest {
  ends_at: number
  preset?: DeepRunPreset
  focus?: DeepRunFocus
  horizon_bias?: DeepRunHorizonBias
  locality?: DeepRunLocality
  cost_cap_usd?: number
  metadata?: Record<string, string>
}

export interface StopDeepRunRequest {
  kill?: boolean
  reason?: string
}
