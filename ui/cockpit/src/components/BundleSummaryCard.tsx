// SPDX-License-Identifier: Apache-2.0
// 0.8.6 WS10 — BundleSummaryCard
//
// Read-only disclosure card for the cockpit Settings drawer. Surfaces the
// currently applied VanerPolicyBundle, its selection reasons, and the
// runner-up bundles so the user can see "why this bundle, not that one"
// without having to re-run the wizard. When the daemon's /policy/current
// endpoint has not yet been wired (WS8 not yet shipped), the parent
// passes `applied_policy={null}` and the card renders a wizard-prompt
// fallback.
//
// Cloud-widening sentinel
// -----------------------
// When `applied_policy.overrides_applied` includes a string starting with
// WIDENS_CLOUD_POSTURE, a yellow warning ribbon is rendered at the top.
// This matches WS5's apply_policy_bundle audit-log contract — see
// src/vaner/setup/apply.py and the WIDENS_CLOUD_POSTURE_SENTINEL constant
// re-exported from ../types/setup.

import { useState } from 'react'

import {
  WIDENS_CLOUD_POSTURE_SENTINEL,
  type AppliedPolicy,
  type VanerPolicyBundle,
} from '../types/setup'

export interface BundleSummaryCardProps {
  applied_policy: AppliedPolicy | null
  selection_reasons: string[]
  runner_ups: VanerPolicyBundle[]
  /** Optional callback for the "Open setup wizard" action. */
  onOpenWizard?: () => void
}

const WIZARD_CLI_COMMAND = 'vaner setup wizard'

function cardStyle(): React.CSSProperties {
  return {
    border: '1px solid var(--line-1)',
    borderRadius: 'var(--r-2, 6px)',
    background: 'var(--bg-1, #18181c)',
    padding: '14px 16px',
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
    color: 'var(--fg-1, #f0f0f0)',
  }
}

function rowStyle(): React.CSSProperties {
  return {
    display: 'flex',
    alignItems: 'baseline',
    justifyContent: 'space-between',
    gap: 10,
  }
}

function pillStyle(): React.CSSProperties {
  return {
    fontSize: 10.5,
    color: 'var(--fg-3, #9a9aa2)',
    fontFamily: 'var(--font-mono, monospace)',
    letterSpacing: 0.4,
  }
}

function disclosureSummaryStyle(open: boolean): React.CSSProperties {
  return {
    cursor: 'pointer',
    fontSize: 12,
    color: open ? 'var(--accent, #5eb2ff)' : 'var(--fg-2, #d0d0d6)',
    fontWeight: 500,
    listStyle: 'none',
  }
}

function widensRibbonStyle(): React.CSSProperties {
  return {
    background: '#3a2d00',
    border: '1px solid var(--amber, #e6b656)',
    borderRadius: 4,
    color: 'var(--amber, #e6b656)',
    padding: '6px 10px',
    fontSize: 11.5,
    lineHeight: 1.4,
  }
}

function buttonStyle(): React.CSSProperties {
  return {
    fontFamily: 'inherit',
    fontSize: 12,
    fontWeight: 600,
    padding: '6px 12px',
    borderRadius: 4,
    border: '1px solid var(--accent, #5eb2ff)',
    background: 'transparent',
    color: 'var(--accent, #5eb2ff)',
    cursor: 'pointer',
  }
}

/**
 * Returns the WIDENS_CLOUD_POSTURE entry from `overrides_applied`, or null.
 * Exported for unit tests that want to assert the sentinel matching logic
 * without re-rendering the full card.
 */
export function findWidensCloudOverride(
  overrides_applied: readonly string[] | undefined,
): string | null {
  if (!overrides_applied) return null
  for (const line of overrides_applied) {
    if (typeof line === 'string' && line.startsWith(WIDENS_CLOUD_POSTURE_SENTINEL)) {
      return line
    }
  }
  return null
}

export function BundleSummaryCard({
  applied_policy,
  selection_reasons,
  runner_ups,
  onOpenWizard,
}: BundleSummaryCardProps) {
  const [showReasons, setShowReasons] = useState(false)
  const [showRunnerUps, setShowRunnerUps] = useState(false)
  const [copied, setCopied] = useState(false)

  if (applied_policy === null) {
    return (
      <section
        aria-label="Vaner setup status"
        role="region"
        style={cardStyle()}
        data-testid="bundle-summary-card-empty"
      >
        <div style={rowStyle()}>
          <strong style={{ fontSize: 13 }}>Setup not yet completed</strong>
        </div>
        <p style={{ margin: 0, fontSize: 12, color: 'var(--fg-2, #d0d0d6)' }}>
          Run <code>{WIZARD_CLI_COMMAND}</code> to begin.
        </p>
        <div>
          <button
            type="button"
            style={buttonStyle()}
            onClick={() => void handleOpenWizard(onOpenWizard, setCopied)}
            aria-label="Copy 'vaner setup wizard' command to clipboard"
          >
            {copied ? 'Copied!' : 'Open setup wizard'}
          </button>
        </div>
      </section>
    )
  }

  const widensOverride = findWidensCloudOverride(applied_policy.overrides_applied)
  const bundleLabel = applied_policy.bundle?.label ?? applied_policy.bundle_id
  const bundleId = applied_policy.bundle_id
  const reasons =
    selection_reasons.length > 0
      ? selection_reasons
      : applied_policy.selection_reasons ?? []
  const runners = runner_ups.length > 0 ? runner_ups : applied_policy.runner_ups ?? []

  return (
    <section
      aria-labelledby="bundle-summary-card-heading"
      role="region"
      style={cardStyle()}
      data-testid="bundle-summary-card"
    >
      {widensOverride ? (
        <div role="alert" style={widensRibbonStyle()} data-testid="bundle-widens-ribbon">
          <strong>Cloud posture widened:</strong> {widensOverride.replace(/^WIDENS_CLOUD_POSTURE:?\s*/, '')}
        </div>
      ) : null}
      <div style={rowStyle()}>
        <h3
          id="bundle-summary-card-heading"
          style={{
            margin: 0,
            fontSize: 13.5,
            fontWeight: 600,
            color: 'var(--fg-1, #f0f0f0)',
          }}
        >
          {bundleLabel} <span style={pillStyle()}>(auto-selected)</span>
        </h3>
        <span style={pillStyle()} data-testid="bundle-id">
          {bundleId}
        </span>
      </div>

      {applied_policy.bundle?.description ? (
        <p style={{ margin: 0, fontSize: 12, color: 'var(--fg-2, #d0d0d6)' }}>
          {applied_policy.bundle.description}
        </p>
      ) : null}

      <details
        open={showReasons}
        onToggle={(event) => setShowReasons((event.currentTarget as HTMLDetailsElement).open)}
      >
        <summary style={disclosureSummaryStyle(showReasons)}>
          Why this bundle? ({reasons.length})
        </summary>
        {reasons.length === 0 ? (
          <p style={{ margin: '8px 0 0', fontSize: 11.5, color: 'var(--fg-3, #9a9aa2)' }}>
            No reasons recorded — the wizard hit its forced-fallback path.
          </p>
        ) : (
          <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 11.5, color: 'var(--fg-2, #d0d0d6)' }}>
            {reasons.map((reason, idx) => (
              <li key={idx}>{reason}</li>
            ))}
          </ul>
        )}
      </details>

      <details
        open={showRunnerUps}
        onToggle={(event) => setShowRunnerUps((event.currentTarget as HTMLDetailsElement).open)}
      >
        <summary style={disclosureSummaryStyle(showRunnerUps)}>
          Other options ({runners.length})
        </summary>
        {runners.length === 0 ? (
          <p style={{ margin: '8px 0 0', fontSize: 11.5, color: 'var(--fg-3, #9a9aa2)' }}>
            No runner-ups recorded.
          </p>
        ) : (
          <ul
            style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 11.5, color: 'var(--fg-2, #d0d0d6)' }}
            aria-label="Runner-up bundles"
          >
            {runners.map((bundle) => (
              <li key={bundle.id}>
                <strong style={{ color: 'var(--fg-1, #f0f0f0)' }}>{bundle.label}</strong>{' '}
                <span style={pillStyle()}>{bundle.id}</span>
              </li>
            ))}
          </ul>
        )}
      </details>

      <div>
        <button
          type="button"
          style={buttonStyle()}
          onClick={() => void handleOpenWizard(onOpenWizard, setCopied)}
          aria-label="Copy 'vaner setup wizard' command to clipboard"
        >
          {copied ? 'Copied!' : 'Open setup wizard'}
        </button>
      </div>
    </section>
  )
}

async function handleOpenWizard(
  onOpenWizard: (() => void) | undefined,
  setCopied: (next: boolean) => void,
): Promise<void> {
  if (onOpenWizard) {
    onOpenWizard()
    return
  }
  // Clipboard fallback — a desktop deep-link will be added in a later WS;
  // for now the user gets the CLI invocation copied to their clipboard.
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(WIZARD_CLI_COMMAND)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
      return
    }
  } catch {
    // Clipboard API may be unavailable in test or insecure contexts.
  }
  // Last-ditch fallback: surface the command in an alert so the user can
  // still copy it manually.
  if (typeof window !== 'undefined' && typeof window.alert === 'function') {
    window.alert(`Run this command in your terminal:\n\n${WIZARD_CLI_COMMAND}`)
  }
}
