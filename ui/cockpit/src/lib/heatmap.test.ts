import { describe, expect, it } from 'vitest'

import {
  buildScenarioHeatmapData,
  filterEventsByType,
  normalizeHeatmapEvents,
  scenarioIntensity,
  scenarioStateAtCursor,
  sortHeatmapRows,
  type HeatmapEvent,
} from './heatmap'
import type { PipelineEvent } from '../api/usePipelineEvents'
import type { UIScenario } from '../types'

function scenario(overrides: Partial<UIScenario>): UIScenario {
  return {
    id: 's1',
    kind: 'change',
    title: 'Scenario',
    score: 0.5,
    relevance: 0.5,
    confidence: 0.5,
    visiblePriority: 0.5,
    freshness: 'recent',
    readiness: 'warming',
    visibility: 'warming',
    lifecycleMotion: 'stable',
    createdAt: 100,
    lastRefreshedAt: 100,
    lastReinforcedAt: 100,
    archivedAt: null,
    visibilityReason: '',
    depth: 0,
    parent: null,
    path: 'src/app.ts',
    skill: null,
    decisionState: 'pending',
    reason: '',
    entities: [],
    pinned: false,
    ...overrides,
  }
}

function event(overrides: Partial<PipelineEvent>): PipelineEvent {
  return {
    id: 'e1',
    t: '00:00',
    tag: 'work',
    color: 'var(--accent)',
    msg: 'Model request completed',
    scn: 's1',
    stage: 'model',
    kind: 'llm.response',
    ts: 100,
    path: 'src/app.ts',
    cycleId: null,
    payload: { latency_ms: 1200 },
    ...overrides,
  }
}

describe('scenarioIntensity', () => {
  it('boosts active scenarios without saturating them', () => {
    const base = scenarioIntensity(scenario({ readiness: 'ready', relevance: 0.8, confidence: 0.7, freshness: 'fresh' }))
    const active = scenarioIntensity(scenario({ readiness: 'ready', relevance: 0.8, confidence: 0.7, freshness: 'fresh' }), 's1')
    expect(active).toBeGreaterThan(base)
    expect(active).toBeLessThan(1)
  })

  it('decays stale rejected scenarios visibly', () => {
    const hot = scenarioIntensity(scenario({ readiness: 'ready', relevance: 0.8, confidence: 0.8, freshness: 'fresh' }))
    const stale = scenarioIntensity(scenario({ readiness: 'ready', relevance: 0.8, confidence: 0.8, freshness: 'stale', decisionState: 'rejected' }))
    expect(stale).toBeLessThan(hot * 0.5)
  })
})

describe('normalizeHeatmapEvents', () => {
  it('turns work snapshots into scenario-linked heatmap events', () => {
    const rows = normalizeHeatmapEvents([
      event({
        id: 'snapshot',
        kind: 'work.snapshot',
        stage: 'work',
        payload: {
          items: [
            {
              event_id: 'lw-1',
              ts: 101,
              entity_type: 'prediction',
              entity_id: 'p1',
              scenario_id: 's2',
              stage: 'model',
              status: 'running',
              summary: 'Model request started',
              targets: ['src/a.ts'],
            },
          ],
        },
      }),
    ])
    expect(rows[0]).toMatchObject({ id: 'lw-1', scenarioId: 's2', type: 'model' })
  })
})

describe('buildScenarioHeatmapData', () => {
  it('generates rows with changing samples and linked events', () => {
    const rows = buildScenarioHeatmapData(
      [scenario({ id: 's1', lastReinforcedAt: 95 })],
      [event({ ts: 100 })],
      {
        now: 120_000,
        rangeMs: 60_000,
        buckets: 32,
        activeScenarioId: 's1',
        samples: [
          { ts: 70, scenario_id: 's1', relevance: 0.3, readiness: 'warming', confidence: 0.5, freshness: 'recent', visible_priority: 0.3, visibility: 'warming', lifecycle_motion: 'stable', status: 'prep', pinned: false, active: false },
          { ts: 110, scenario_id: 's1', relevance: 0.8, readiness: 'ready', confidence: 0.7, freshness: 'fresh', visible_priority: 0.8, visibility: 'prominent', lifecycle_motion: 'rising', status: 'active', pinned: false, active: true },
        ],
      },
    )
    expect(rows).toHaveLength(1)
    expect(rows[0].samples).toHaveLength(2)
    expect(rows[0].events).toHaveLength(1)
    expect(Math.max(...rows[0].samples.map((sample) => sample.intensity))).toBeGreaterThan(
      Math.min(...rows[0].samples.map((sample) => sample.intensity)),
    )
  })

  it('can filter stale and eventless rows', () => {
    const rows = buildScenarioHeatmapData(
      [
        scenario({ id: 'hot', freshness: 'fresh' }),
        scenario({ id: 'old', freshness: 'stale' }),
      ],
      [],
      { now: 120_000, rangeMs: 60_000, showStale: false, onlyWithEvents: true },
    )
    expect(rows).toHaveLength(0)
  })
})

describe('scenarioStateAtCursor', () => {
  it('returns the latest sample at or before the cursor', () => {
    const row = buildScenarioHeatmapData(
      [scenario({ id: 's1' })],
      [],
      {
        now: 120_000,
        rangeMs: 60_000,
        samples: [
          { ts: 70, scenario_id: 's1', relevance: 0.2, readiness: 'warming', confidence: 0.4, freshness: 'recent', visible_priority: 0.2, visibility: 'warming', lifecycle_motion: 'stable', status: 'prep', pinned: false, active: false },
          { ts: 90, scenario_id: 's1', relevance: 0.7, readiness: 'ready', confidence: 0.8, freshness: 'fresh', visible_priority: 0.7, visibility: 'prominent', lifecycle_motion: 'rising', status: 'ready', pinned: false, active: false },
        ],
      },
    )[0]
    const sample = scenarioStateAtCursor(row, 91_000)
    expect(sample).toBe(row.samples[1])
  })
})

describe('filtering and sorting', () => {
  it('filters events by enabled type', () => {
    const events: HeatmapEvent[] = [
      { id: 'a', timestamp: 1, scenarioId: 's1', type: 'model', title: '', description: '', severity: 'info', magnitude: 0.5, paths: [] },
      { id: 'b', timestamp: 2, scenarioId: 's1', type: 'error', title: '', description: '', severity: 'error', magnitude: 0.8, paths: [] },
    ]
    expect(filterEventsByType(events, new Set(['error']))).toHaveLength(1)
  })

  it('sorts active rows first', () => {
    const rows = buildScenarioHeatmapData(
      [scenario({ id: 'a', relevance: 0.2 }), scenario({ id: 'b', relevance: 0.9 })],
      [],
      { now: 120_000, rangeMs: 60_000, activeScenarioId: 'a' },
    )
    expect(sortHeatmapRows(rows, 'active')[0].scenario.id).toBe('a')
  })
})
