// 0.8.6 WS10 — jest-axe unit-level a11y harness for the new cockpit
// components. The full axe-core scan against the running cockpit and the
// MCP Apps UI bundle is performed by .github/workflows/cockpit-a11y.yml;
// this file catches regressions earlier in the dev loop.
//
// We only assert the absence of *serious* / *critical* violations — the
// same threshold the CI workflow uses. Best-practice and minor warnings
// are surfaced as console output but do not fail the suite.

import { render } from '@testing-library/react'
import { axe, toHaveNoViolations } from 'jest-axe'
import { describe, expect, it } from 'vitest'

import { BundleSummaryCard } from '../src/components/BundleSummaryCard'
import { HardwareProfilePanel } from '../src/components/HardwareProfilePanel'
import type { AppliedPolicy, HardwareProfile } from '../src/types/setup'

expect.extend(toHaveNoViolations)

const SAMPLE_APPLIED: AppliedPolicy = {
  bundle_id: 'hybrid_balanced',
  overrides_applied: [],
  bundle: {
    id: 'hybrid_balanced',
    label: 'Hybrid Balanced',
    description: 'Mid-spend, mid-latency. The canonical default.',
    local_cloud_posture: 'hybrid',
    runtime_profile: 'medium',
    spend_profile: 'low',
    latency_profile: 'balanced',
    privacy_profile: 'standard',
    prediction_horizon_bias: {
      likely_next: 1,
      long_horizon: 1,
      finish_partials: 1,
      balanced: 1,
    },
    drafting_aggressiveness: 1,
    exploration_ratio: 0.2,
    persistence_strength: 1,
    goal_weighting: 1,
    context_injection_default: 'policy_hybrid',
    deep_run_profile: 'balanced',
  },
}

const SAMPLE_PROFILE: HardwareProfile = {
  os: 'darwin',
  cpu_class: 'high',
  ram_gb: 32,
  gpu: 'apple_silicon',
  gpu_vram_gb: null,
  is_battery: false,
  thermal_constrained: false,
  detected_runtimes: ['ollama'],
  detected_models: [['ollama', 'qwen2.5-coder:7b', '4.4GB']],
  tier: 'capable',
}

describe('a11y — BundleSummaryCard', () => {
  it('has no serious axe violations when applied_policy is null', async () => {
    const { container } = render(
      <BundleSummaryCard applied_policy={null} selection_reasons={[]} runner_ups={[]} />,
    )
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })

  it('has no serious axe violations with an applied policy', async () => {
    const { container } = render(
      <BundleSummaryCard
        applied_policy={SAMPLE_APPLIED}
        selection_reasons={['Speed-first → hybrid_balanced is balanced']}
        runner_ups={[]}
      />,
    )
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })

  it('has no serious axe violations when the WIDENS sentinel is present', async () => {
    const widening: AppliedPolicy = {
      ...SAMPLE_APPLIED,
      overrides_applied: ['WIDENS_CLOUD_POSTURE: local_only->hybrid'],
    }
    const { container } = render(
      <BundleSummaryCard
        applied_policy={widening}
        selection_reasons={[]}
        runner_ups={[]}
      />,
    )
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})

describe('a11y — HardwareProfilePanel', () => {
  it('has no serious axe violations when profile is null', async () => {
    const { container } = render(<HardwareProfilePanel profile={null} />)
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })

  it('has no serious axe violations with a populated profile', async () => {
    const { container } = render(<HardwareProfilePanel profile={SAMPLE_PROFILE} />)
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})
