import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Inspector } from './Inspector'
import type { UIScenario } from '../types'

const scenario: UIScenario = {
  id: 'scn_1',
  kind: 'research',
  title: 'Frontier adaptation logic',
  score: 0.91,
  freshness: 'fresh',
  depth: 0,
  parent: null,
  path: 'src/vaner/frontier/adapt.py',
  skill: 'vaner-research',
  decisionState: 'chosen',
  reason: 'highest semantic overlap',
  entities: ['PonderLoop'],
  pinned: true,
}

describe('Inspector', () => {
  it('renders verdict and evidence for the selected scenario', () => {
    render(
      <Inspector
        scenario={scenario}
        scenarios={[scenario]}
        evidenceById={{
          scn_1: [
            {
              file: 'src/vaner/frontier/adapt.py',
              lines: '10-12',
              note: 'reweight() called on feedback',
              startLine: 10,
              endLine: 12,
            },
          ],
        }}
        scoreComponentsById={{
          scn_1: [
            { label: 'semantic overlap', value: 0.42 },
            { label: 'freshness', value: 0.12 },
          ],
        }}
        preparedById={{ scn_1: '{"file_summary":"reweight"}' }}
        onSelect={() => undefined}
        onFeedback={() => undefined}
        onPin={() => undefined}
        pinnedIds={new Set(['scn_1'])}
        onClose={() => undefined}
      />,
    )

    expect(screen.getByText('Frontier adaptation logic')).toBeInTheDocument()
    expect(screen.getByText('CHOSEN')).toBeInTheDocument()
    expect(screen.getAllByText('reweight() called on feedback').length).toBeGreaterThan(0)
  })
})
