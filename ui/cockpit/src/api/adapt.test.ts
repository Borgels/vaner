import { describe, expect, it } from 'vitest'

import { adaptEvidence, adaptScenario, deriveDecisionState } from './adapt'

describe('adaptScenario', () => {
  it('derives title, path, decision state, and reason from backend payloads', () => {
    const adapted = adaptScenario({
      id: 'scn_123',
      kind: 'change',
      score: 0.82,
      freshness: 'fresh',
      entities: ['src/vaner/router/proxy.py'],
      evidence: [],
      coverage_gaps: ['recent diff overlap'],
      last_outcome: 'useful',
      pinned: 0,
    })

    expect(adapted.title).toBe('proxy.py')
    expect(adapted.path).toBe('src/vaner/router/proxy.py')
    expect(adapted.decisionState).toBe('chosen')
    expect(adapted.reason).toBe('recent diff overlap')
  })

  it('prefers explicit decision_state and pinned values', () => {
    expect(
      deriveDecisionState({
        id: 'scn_456',
        kind: 'research',
        score: 0.1,
        freshness: 'recent',
        entities: [],
        evidence: [],
        decision_state: 'active',
        pinned: true,
      }),
    ).toBe('active')
  })
})

describe('adaptEvidence', () => {
  it('formats line ranges when both start and end are provided', () => {
    const evidence = adaptEvidence({
      id: 'scn_abc',
      kind: 'change',
      score: 0.5,
      freshness: 'fresh',
      entities: [],
      evidence: [
        { source_path: 'src/a.py', excerpt: 'return x', start_line: 10, end_line: 12 },
      ],
    })
    expect(evidence[0].lines).toBe('10-12')
    expect(evidence[0].startLine).toBe(10)
    expect(evidence[0].endLine).toBe(12)
  })

  it('keeps lines null when the backend has no line info', () => {
    const evidence = adaptEvidence({
      id: 'scn_abc',
      kind: 'change',
      score: 0.5,
      freshness: 'fresh',
      entities: [],
      evidence: [{ source_path: 'src/a.py', excerpt: 'return x' }],
    })
    expect(evidence[0].lines).toBeNull()
    expect(evidence[0].startLine).toBeNull()
    expect(evidence[0].endLine).toBeNull()
  })

  it('collapses identical start/end to a single line label', () => {
    const evidence = adaptEvidence({
      id: 'scn_abc',
      kind: 'change',
      score: 0.5,
      freshness: 'fresh',
      entities: [],
      evidence: [
        { source_path: 'src/a.py', excerpt: 'return x', start_line: 7, end_line: 7 },
      ],
    })
    expect(evidence[0].lines).toBe('7')
  })
})
