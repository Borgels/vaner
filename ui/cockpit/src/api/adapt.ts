import type { ScenarioApiPayload, UIEvidence, UIScenario } from '../types'

function lastPathSegment(path: string): string {
  const trimmed = path.trim()
  if (!trimmed) {
    return ''
  }

  const parts = trimmed.split('/')
  return parts[parts.length - 1] ?? trimmed
}

export function deriveDecisionState(payload: ScenarioApiPayload): UIScenario['decisionState'] {
  if (payload.decision_state) {
    return payload.decision_state
  }

  if (payload.pinned) {
    return 'chosen'
  }

  if (payload.memory_state === 'demoted') {
    return 'rejected'
  }

  switch (payload.last_outcome) {
    case 'useful':
      return 'chosen'
    case 'partial':
      return 'partial'
    case 'irrelevant':
    case 'wrong':
      return 'rejected'
    default:
      return 'pending'
  }
}

export function adaptScenario(payload: ScenarioApiPayload): UIScenario {
  const path = payload.path ?? payload.entities[0] ?? ''
  const title = payload.title ?? (lastPathSegment(path) || payload.id)
  const decisionState = deriveDecisionState(payload)
  const reason =
    payload.reason ??
    payload.coverage_gaps?.[0] ??
    `${payload.kind} · score ${Number(payload.score ?? 0).toFixed(3)}`

  return {
    id: payload.id,
    kind: payload.kind,
    title,
    score: Number(payload.score ?? 0),
    freshness: payload.freshness ?? 'recent',
    depth: payload.depth ?? 0,
    parent: payload.parent ?? null,
    path,
    skill: payload.skill ?? null,
    decisionState,
    reason,
    entities: payload.entities ?? [],
    pinned: Boolean(payload.pinned),
  }
}

function formatLineRange(start: number | null | undefined, end: number | null | undefined): string | null {
  if (start == null && end == null) {
    return null
  }

  if (start != null && end != null && end !== start) {
    return `${start}-${end}`
  }

  return String(start ?? end)
}

export function adaptEvidence(payload: ScenarioApiPayload): UIEvidence[] {
  return (payload.evidence ?? []).map((item) => ({
    file: item.source_path ?? item.key ?? 'unknown',
    lines: formatLineRange(item.start_line, item.end_line),
    note: item.excerpt ?? '',
    startLine: item.start_line ?? null,
    endLine: item.end_line ?? null,
  }))
}
