import { describe, expect, it } from 'vitest'

import { adaptScenario, scenarioDisplayTitle } from './adapt'
import type { ScenarioApiPayload } from '../types'

function payload(overrides: Partial<ScenarioApiPayload> = {}): ScenarioApiPayload {
  return {
    id: 'scn_38b0bc6ad011f891',
    kind: 'research',
    score: 0.91,
    freshness: 'fresh',
    entities: [],
    evidence: [],
    ...overrides,
  }
}

describe('scenarioDisplayTitle', () => {
  it('does not surface raw scn ids when reason text is available', () => {
    expect(
      scenarioDisplayTitle(
        payload({
          title: 'scn_38b0bc6ad011f891',
          reason: 'Investigate why the ponder loop drops scenarios after reweight',
        }),
      ),
    ).toBe('Investigate why the ponder loop drops scenarios after reweight')
  })

  it('falls back to evidence excerpts before raw identifiers', () => {
    expect(
      scenarioDisplayTitle(
        payload({
          evidence: [{ excerpt: 'Prepared work changed the client wiring flow', source_path: 'src/vaner/cli/commands/launch.py' }],
        }),
      ),
    ).toBe('Prepared work changed the client wiring flow')
  })

  it('uses a short path label when no prose exists', () => {
    expect(
      adaptScenario(
        payload({
          path: 'src/vaner/cli/commands/launch.py',
        }),
      ).title,
    ).toBe('Inspect commands/launch.py')
  })

  it('turns raw release diffs into actionable labels', () => {
    expect(
      scenarioDisplayTitle(
        payload({
          kind: 'change',
          evidence: [
            {
              source_path: 'pyproject.toml',
              excerpt: 'Snippet: diff --git a/pyproject.toml b/pyproject.toml @@ -version = "0.8.9" +version = "0.9.0"',
            },
          ],
        }),
      ),
    ).toBe('Update version 0.8.9 -> 0.9.0 in pyproject.toml')
  })

  it('summarizes grouped changed files instead of showing snippets', () => {
    expect(
      scenarioDisplayTitle(
        payload({
          kind: 'change',
          entities: ['CHANGELOG.md', 'pyproject.toml'],
          evidence: [
            {
              source_path: 'CHANGELOG.md',
              excerpt: 'Snippet: diff --git a/CHANGELOG.md b/CHANGELOG.md',
            },
          ],
        }),
      ),
    ).toBe('Update related changes in CHANGELOG.md and pyproject.toml')
  })

  it('summarizes same-directory file clusters by area', () => {
    expect(
      scenarioDisplayTitle(
        payload({
          kind: 'change',
          entities: [
            'ui/cockpit/src/components/PipelineCanvas.tsx',
            'ui/cockpit/src/components/ScenarioCluster.tsx',
            'ui/cockpit/src/components/chrome.tsx',
          ],
        }),
      ),
    ).toBe('Update 3 files in ui/cockpit/src/components')
  })

  it('fills a useful reason when the daemon did not provide one', () => {
    const adapted = adaptScenario(
      payload({
        kind: 'change',
        entities: ['CHANGELOG.md', 'pyproject.toml'],
        evidence: [
          {
            source_path: 'CHANGELOG.md',
            excerpt: 'Snippet: diff --git a/CHANGELOG.md b/CHANGELOG.md',
          },
          {
            source_path: 'pyproject.toml',
            excerpt: 'Snippet: diff --git a/pyproject.toml b/pyproject.toml',
          },
        ],
      }),
    )

    expect(adapted.reason).toContain('Based on 2 change summaries')
    expect(adapted.reason).toContain('CHANGELOG.md and pyproject.toml')
  })

  it('maps lifecycle fields for the live scenario map', () => {
    const adapted = adaptScenario(
      payload({
        score: 0.8,
        confidence: 0.72,
        relevance: 0.91,
        visible_priority: 0.89,
        readiness: 'ready',
        visibility: 'prominent',
        lifecycle_motion: 'rising',
        last_reinforced_at: 12,
        archived_at: null,
        visibility_reason: 'prepared context is ready',
      }),
    )

    expect(adapted.relevance).toBe(0.91)
    expect(adapted.confidence).toBe(0.72)
    expect(adapted.visiblePriority).toBe(0.89)
    expect(adapted.readiness).toBe('ready')
    expect(adapted.visibility).toBe('prominent')
    expect(adapted.lifecycleMotion).toBe('rising')
    expect(adapted.visibilityReason).toBe('prepared context is ready')
  })
})
