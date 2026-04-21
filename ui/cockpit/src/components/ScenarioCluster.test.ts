import { describe, expect, it } from 'vitest'

import { computeEdges, initialLayout, jaccard, scenarioPaths, tickForces } from './ScenarioCluster'
import type { UIScenario } from '../types'

function scenario(overrides: Partial<UIScenario>): UIScenario {
  return {
    id: 'x',
    kind: 'research',
    title: 'x',
    score: 0.5,
    freshness: 'fresh',
    depth: 0,
    parent: null,
    path: 'x.py',
    skill: null,
    decisionState: 'pending',
    reason: 'because',
    entities: [],
    pinned: false,
    ...overrides,
  }
}

describe('jaccard', () => {
  it('returns 0 for disjoint sets', () => {
    expect(jaccard(new Set(['a']), new Set(['b']))).toBe(0)
  })

  it('returns 1 for identical sets', () => {
    expect(jaccard(new Set(['a', 'b']), new Set(['a', 'b']))).toBe(1)
  })

  it('computes partial overlap correctly', () => {
    expect(jaccard(new Set(['a', 'b']), new Set(['a', 'c']))).toBeCloseTo(1 / 3, 5)
  })

  it('returns 0 when either set is empty', () => {
    expect(jaccard(new Set(), new Set(['a']))).toBe(0)
  })
})

describe('scenarioPaths', () => {
  it('includes both path and entities', () => {
    const paths = scenarioPaths(
      scenario({ id: 'a', path: 'src/main.py', entities: ['src/util.py', 'README.md'] }),
    )
    expect(paths).toEqual(new Set(['src/main.py', 'src/util.py', 'README.md']))
  })
})

describe('computeEdges', () => {
  it('includes explicit parent edges regardless of path overlap', () => {
    const edges = computeEdges([
      scenario({ id: 'root', path: 'root.py' }),
      scenario({ id: 'child', parent: 'root', path: 'child.py' }),
    ])
    expect(edges).toContainEqual(
      expect.objectContaining({ from: 'root', to: 'child', kind: 'parent' }),
    )
  })

  it('derives shared-path edges above the threshold', () => {
    const edges = computeEdges([
      scenario({ id: 'a', path: 'src/api.py', entities: ['src/util.py'] }),
      scenario({ id: 'b', path: 'src/api.py', entities: ['src/util.py'] }),
    ])
    const sharedEdges = edges.filter((edge) => edge.kind === 'shared-path')
    expect(sharedEdges.length).toBeGreaterThan(0)
    expect(sharedEdges[0].weight).toBeGreaterThan(0.5)
  })

  it('omits shared-path edges below the threshold', () => {
    const edges = computeEdges(
      [
        scenario({ id: 'a', path: 'src/a.py' }),
        scenario({ id: 'b', path: 'src/b.py' }),
      ],
      { threshold: 0.5 },
    )
    expect(edges.filter((edge) => edge.kind === 'shared-path')).toHaveLength(0)
  })

  it('limits shared-path edges per node', () => {
    const scenarios = Array.from({ length: 8 }, (_, index) =>
      scenario({ id: `s${index}`, path: 'shared.py', entities: ['e.py'] }),
    )
    const edges = computeEdges(scenarios, { threshold: 0.1, maxPerNode: 2 })
    const shared = edges.filter((edge) => edge.kind === 'shared-path')
    // Each of the 8 nodes contributes up to ``maxPerNode`` outbound edges
    // before dedup; the total shared-path edges must be well below the
    // quadratic ceiling of ``n*(n-1)/2`` (28).
    expect(shared.length).toBeLessThanOrEqual(16)
  })
})

describe('initialLayout', () => {
  it('places every scenario within the viewport bounds', () => {
    const scenarios = [
      scenario({ id: 'a', kind: 'research' }),
      scenario({ id: 'b', kind: 'change' }),
      scenario({ id: 'c', kind: 'debug' }),
    ]
    const positions = initialLayout(scenarios, 800, 600)
    for (const id of ['a', 'b', 'c']) {
      expect(positions[id]).toBeDefined()
      expect(positions[id].x).toBeGreaterThan(0)
      expect(positions[id].y).toBeGreaterThan(0)
      expect(positions[id].x).toBeLessThan(800)
      expect(positions[id].y).toBeLessThan(600)
    }
  })

  it('buckets scenarios of the same kind into the same angular sector', () => {
    const scenarios = [
      scenario({ id: 'a', kind: 'research', score: 0.6 }),
      scenario({ id: 'b', kind: 'research', score: 0.7 }),
      scenario({ id: 'c', kind: 'debug', score: 0.8 }),
    ]
    const positions = initialLayout(scenarios, 800, 600)
    const angle = (id: string) =>
      Math.atan2(positions[id].y - 300, positions[id].x - 400)
    const researchGap = Math.abs(angle('a') - angle('b'))
    const crossKindGap = Math.abs(angle('a') - angle('c'))
    expect(researchGap).toBeLessThan(crossKindGap)
  })
})

describe('tickForces', () => {
  it('keeps parent-linked nodes within a bounded range after several ticks', () => {
    const scenarios = [
      scenario({ id: 'root' }),
      scenario({ id: 'child', parent: 'root' }),
    ]
    const positions = initialLayout(scenarios, 800, 600)
    const edges = computeEdges(scenarios)
    for (let tick = 0; tick < 60; tick += 1) {
      tickForces(positions, edges, { width: 800, height: 600 })
    }
    const dx = positions.root.x - positions.child.x
    const dy = positions.root.y - positions.child.y
    const distance = Math.sqrt(dx * dx + dy * dy)
    expect(distance).toBeGreaterThan(40)
    expect(distance).toBeLessThan(400)
  })

  it('does not move nodes marked as manual', () => {
    const scenarios = [scenario({ id: 'a' }), scenario({ id: 'b' })]
    const positions = initialLayout(scenarios, 800, 600)
    positions.a.x = 100
    positions.a.y = 100
    positions.a._manual = true
    const edges = computeEdges(scenarios)
    for (let tick = 0; tick < 10; tick += 1) {
      tickForces(positions, edges, { width: 800, height: 600 })
    }
    expect(positions.a.x).toBe(100)
    expect(positions.a.y).toBe(100)
  })
})
