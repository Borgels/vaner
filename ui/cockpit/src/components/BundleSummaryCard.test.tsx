import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import {
  BundleSummaryCard,
  findWidensCloudOverride,
} from './BundleSummaryCard'
import type { AppliedPolicy, VanerPolicyBundle } from '../types/setup'

function makeBundle(overrides: Partial<VanerPolicyBundle> = {}): VanerPolicyBundle {
  return {
    id: overrides.id ?? 'hybrid_balanced',
    label: overrides.label ?? 'Hybrid Balanced',
    description:
      overrides.description ??
      'Mid-spend, mid-latency. The canonical default for "I just want it to work."',
    local_cloud_posture: overrides.local_cloud_posture ?? 'hybrid',
    runtime_profile: overrides.runtime_profile ?? 'medium',
    spend_profile: overrides.spend_profile ?? 'low',
    latency_profile: overrides.latency_profile ?? 'balanced',
    privacy_profile: overrides.privacy_profile ?? 'standard',
    prediction_horizon_bias:
      overrides.prediction_horizon_bias ?? {
        likely_next: 1,
        long_horizon: 1,
        finish_partials: 1,
        balanced: 1,
      },
    drafting_aggressiveness: overrides.drafting_aggressiveness ?? 1,
    exploration_ratio: overrides.exploration_ratio ?? 0.2,
    persistence_strength: overrides.persistence_strength ?? 1,
    goal_weighting: overrides.goal_weighting ?? 1,
    context_injection_default:
      overrides.context_injection_default ?? 'policy_hybrid',
    deep_run_profile: overrides.deep_run_profile ?? 'balanced',
  }
}

function makeApplied(overrides: Partial<AppliedPolicy> = {}): AppliedPolicy {
  return {
    bundle_id: overrides.bundle_id ?? 'hybrid_balanced',
    overrides_applied: overrides.overrides_applied ?? [],
    bundle: overrides.bundle ?? makeBundle(),
    selection_reasons: overrides.selection_reasons,
    runner_ups: overrides.runner_ups,
  }
}

describe('BundleSummaryCard', () => {
  it('renders the wizard-prompt fallback when applied_policy is null', () => {
    render(
      <BundleSummaryCard
        applied_policy={null}
        selection_reasons={[]}
        runner_ups={[]}
      />,
    )
    expect(screen.getByText(/Setup not yet completed/i)).toBeInTheDocument()
    expect(screen.getByText('vaner setup wizard')).toBeInTheDocument()
  })

  it('renders the bundle label and id when a policy is applied', () => {
    render(
      <BundleSummaryCard
        applied_policy={makeApplied()}
        selection_reasons={['Speed-first → hybrid_balanced is balanced']}
        runner_ups={[]}
      />,
    )
    expect(screen.getByText(/Hybrid Balanced/)).toBeInTheDocument()
    expect(screen.getByTestId('bundle-id')).toHaveTextContent('hybrid_balanced')
  })

  it('renders the cloud-widening warning ribbon when sentinel is present', () => {
    const applied = makeApplied({
      overrides_applied: [
        'WIDENS_CLOUD_POSTURE: local_only->hybrid',
        'BackendConfig.prefer_local: false',
      ],
    })
    render(
      <BundleSummaryCard
        applied_policy={applied}
        selection_reasons={[]}
        runner_ups={[]}
      />,
    )
    const ribbon = screen.getByTestId('bundle-widens-ribbon')
    expect(ribbon).toBeInTheDocument()
    expect(ribbon).toHaveTextContent(/local_only->hybrid/)
    expect(ribbon).toHaveAttribute('role', 'alert')
  })

  it('does not render the warning ribbon when sentinel is absent', () => {
    render(
      <BundleSummaryCard
        applied_policy={makeApplied({
          overrides_applied: ['BackendConfig.prefer_local: true'],
        })}
        selection_reasons={[]}
        runner_ups={[]}
      />,
    )
    expect(screen.queryByTestId('bundle-widens-ribbon')).not.toBeInTheDocument()
  })

  it('expands the "Why this bundle?" disclosure to show reasons', () => {
    render(
      <BundleSummaryCard
        applied_policy={makeApplied()}
        selection_reasons={['Reason A', 'Reason B']}
        runner_ups={[]}
      />,
    )
    expect(screen.getByText(/Why this bundle\? \(2\)/)).toBeInTheDocument()
    // Reasons render inside the <details>, so they are always in the DOM
    // (jsdom doesn't hide closed-details children from queries).
    expect(screen.getByText('Reason A')).toBeInTheDocument()
    expect(screen.getByText('Reason B')).toBeInTheDocument()
  })

  it('renders runner-up bundle labels when provided', () => {
    const runner = makeBundle({ id: 'cost_saver', label: 'Cost Saver' })
    render(
      <BundleSummaryCard
        applied_policy={makeApplied()}
        selection_reasons={[]}
        runner_ups={[runner]}
      />,
    )
    expect(screen.getByText('Cost Saver')).toBeInTheDocument()
    expect(screen.getByText('cost_saver')).toBeInTheDocument()
  })

  it('calls onOpenWizard when provided instead of using the clipboard', () => {
    const onOpenWizard = vi.fn()
    render(
      <BundleSummaryCard
        applied_policy={null}
        selection_reasons={[]}
        runner_ups={[]}
        onOpenWizard={onOpenWizard}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /Copy/ }))
    expect(onOpenWizard).toHaveBeenCalledTimes(1)
  })

  it('exposes a section role with an aria-label', () => {
    render(
      <BundleSummaryCard
        applied_policy={makeApplied()}
        selection_reasons={[]}
        runner_ups={[]}
      />,
    )
    expect(screen.getByRole('region')).toBeInTheDocument()
  })
})

describe('findWidensCloudOverride', () => {
  it('returns the matching line when the sentinel is present', () => {
    expect(
      findWidensCloudOverride([
        'BackendConfig.prefer_local: false',
        'WIDENS_CLOUD_POSTURE: local_only->cloud_preferred',
      ]),
    ).toBe('WIDENS_CLOUD_POSTURE: local_only->cloud_preferred')
  })

  it('returns null when the sentinel is absent', () => {
    expect(findWidensCloudOverride(['info: bundle.privacy_profile=strict'])).toBeNull()
  })

  it('returns null on undefined input', () => {
    expect(findWidensCloudOverride(undefined)).toBeNull()
  })
})
