import type { ScenarioApiPayload, UIEvidence, UIScenario } from '../types'

export function adaptScenario(payload: ScenarioApiPayload): UIScenario {
  return {
    id: payload.id,
    kind: payload.kind ?? 'research',
    title: payload.title || payload.id,
    score: Number(payload.score ?? 0),
    freshness: payload.freshness ?? 'recent',
    depth: Number(payload.depth ?? 0),
    parent: payload.parent ?? null,
    path: payload.path ?? payload.evidence?.[0]?.source_path ?? '',
    skill: payload.skill ?? null,
    decisionState: payload.decision_state ?? 'active',
    reason: payload.reason ?? '',
    entities: payload.entities ?? [],
    pinned: Boolean(payload.pinned),
  }
}

export function adaptEvidence(payload: ScenarioApiPayload): UIEvidence[] {
  return (payload.evidence ?? []).map((item) => ({
    file: item.source_path ?? item.key ?? '',
    lines: item.start_line || item.end_line ? `${item.start_line ?? '?'}-${item.end_line ?? '?'}` : null,
    note: item.excerpt ?? '',
    startLine: item.start_line ?? null,
    endLine: item.end_line ?? null,
  }))
}
