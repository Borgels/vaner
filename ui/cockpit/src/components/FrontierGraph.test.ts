import { describe, expect, it } from 'vitest'

import { computeLayout } from './FrontierGraph'
import type { UIScenario } from '../types'

describe('computeLayout', () => {
  it('places child scenarios to the right of their parents', () => {
    const scenarios: UIScenario[] = [
      {
        id: 'root',
        kind: 'research',
        title: 'Root',
        score: 0.9,
        freshness: 'fresh',
        depth: 0,
        parent: null,
        path: 'root.py',
        skill: null,
        decisionState: 'pending',
        reason: 'root',
        entities: [],
        pinned: false,
      },
      {
        id: 'child',
        kind: 'change',
        title: 'Child',
        score: 0.8,
        freshness: 'recent',
        depth: 1,
        parent: 'root',
        path: 'child.py',
        skill: null,
        decisionState: 'pending',
        reason: 'child',
        entities: [],
        pinned: false,
      },
    ]

    const positions = computeLayout(scenarios, 1000, 700)

    expect(positions.child.x).toBeGreaterThan(positions.root.x)
  })
})
