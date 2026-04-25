// SPDX-License-Identifier: Apache-2.0
// 0.8.6 WS10 — HardwareProfilePanel
//
// Renders the daemon-side HardwareProfile snapshot (see WS2 in
// src/vaner/setup/hardware.py). Surfaced inside SystemVitals' "Device"
// section so the user can see why a particular policy bundle was
// auto-selected and which local runtimes / models the daemon detected.
//
// Until WS8 ships /hardware/profile, the parent passes `profile={null}`
// and the panel renders a "Hardware probe unavailable on this daemon"
// fallback. The Refresh button delegates to the parent's onRefresh
// callback so the parent can choose how to surface a 404 (toast,
// silent, etc.).

import { useState } from 'react'

import type {
  CPUClass,
  DetectedModel,
  GPUKind,
  HardwareProfile,
  HardwareTier,
  Runtime,
} from '../types/setup'

export interface HardwareProfilePanelProps {
  profile: HardwareProfile | null
  /** Optional refresh hook. When omitted, the refresh button is hidden. */
  onRefresh?: () => Promise<void> | void
  /** When true, the refresh action is in flight. */
  refreshing?: boolean
}

const TIER_LABEL: Record<HardwareTier, string> = {
  light: 'Light device',
  capable: 'Capable device',
  high_performance: 'High-performance device',
  unknown: 'Unknown device',
}

const GPU_LABEL: Record<GPUKind, string> = {
  none: 'No GPU',
  integrated: 'Integrated GPU',
  nvidia: 'NVIDIA GPU',
  amd: 'AMD GPU',
  apple_silicon: 'Apple Silicon GPU',
}

const CPU_LABEL: Record<CPUClass, string> = {
  low: 'Low-end CPU',
  mid: 'Mid-range CPU',
  high: 'High-end CPU',
}

function panelStyle(): React.CSSProperties {
  return {
    padding: '14px 16px',
    borderTop: '1px solid var(--line-hair, #2a2a2f)',
    background: 'var(--bg-0, #101013)',
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
    color: 'var(--fg-1, #f0f0f0)',
  }
}

function chipStyle(): React.CSSProperties {
  return {
    display: 'inline-block',
    fontFamily: 'var(--font-mono, monospace)',
    fontSize: 10.5,
    background: 'var(--bg-inset, #1a1a1f)',
    border: '1px solid var(--line-1, #2a2a2f)',
    borderRadius: 999,
    padding: '2px 8px',
    color: 'var(--fg-2, #d0d0d6)',
  }
}

function refreshButtonStyle(): React.CSSProperties {
  return {
    fontFamily: 'inherit',
    fontSize: 11,
    fontWeight: 500,
    padding: '4px 10px',
    borderRadius: 4,
    border: '1px solid var(--line-1, #2a2a2f)',
    background: 'transparent',
    color: 'var(--fg-2, #d0d0d6)',
    cursor: 'pointer',
  }
}

function summaryLine(profile: HardwareProfile): string {
  const parts: string[] = []
  parts.push(TIER_LABEL[profile.tier] ?? 'Device')
  if (profile.ram_gb > 0) {
    parts.push(`${profile.ram_gb} GB RAM`)
  }
  if (profile.gpu !== 'none') {
    const gpuLabel = GPU_LABEL[profile.gpu] ?? 'GPU'
    if (profile.gpu_vram_gb && profile.gpu_vram_gb > 0) {
      parts.push(`${gpuLabel} (${profile.gpu_vram_gb} GB VRAM)`)
    } else {
      parts.push(gpuLabel)
    }
  }
  parts.push(CPU_LABEL[profile.cpu_class] ?? 'CPU')
  if (profile.is_battery) {
    parts.push('on battery')
  }
  if (profile.thermal_constrained) {
    parts.push('thermal-constrained')
  }
  return parts.join(' · ')
}

export function HardwareProfilePanel({
  profile,
  onRefresh,
  refreshing,
}: HardwareProfilePanelProps) {
  const [showModels, setShowModels] = useState(false)

  if (profile === null) {
    return (
      <section
        aria-labelledby="hardware-profile-heading"
        role="region"
        style={panelStyle()}
        data-testid="hardware-profile-panel-unavailable"
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <h3
            id="hardware-profile-heading"
            style={{ margin: 0, fontSize: 11, letterSpacing: 1.2, color: 'var(--fg-4, #6f6f78)' }}
          >
            DEVICE
          </h3>
          {onRefresh ? (
            <button
              type="button"
              style={refreshButtonStyle()}
              onClick={() => void onRefresh()}
              disabled={refreshing === true}
              aria-label="Re-run hardware probe"
            >
              {refreshing ? 'Refreshing…' : 'Refresh'}
            </button>
          ) : null}
        </div>
        <p style={{ margin: 0, fontSize: 12, color: 'var(--fg-2, #d0d0d6)' }}>
          Hardware probe unavailable on this daemon.
        </p>
      </section>
    )
  }

  const runtimes: Runtime[] = profile.detected_runtimes ?? []
  const models: DetectedModel[] = profile.detected_models ?? []

  return (
    <section
      aria-labelledby="hardware-profile-heading"
      role="region"
      style={panelStyle()}
      data-testid="hardware-profile-panel"
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <h3
          id="hardware-profile-heading"
          style={{ margin: 0, fontSize: 11, letterSpacing: 1.2, color: 'var(--fg-4, #6f6f78)' }}
        >
          DEVICE
        </h3>
        {onRefresh ? (
          <button
            type="button"
            style={refreshButtonStyle()}
            onClick={() => void onRefresh()}
            disabled={refreshing === true}
            aria-label="Re-run hardware probe"
          >
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        ) : null}
      </div>

      <p style={{ margin: 0, fontSize: 12.5, color: 'var(--fg-1, #f0f0f0)' }}>
        {summaryLine(profile)}
      </p>

      <div>
        <div
          style={{ fontSize: 10.5, color: 'var(--fg-4, #6f6f78)', letterSpacing: 0.6, marginBottom: 4 }}
        >
          DETECTED RUNTIMES
        </div>
        {runtimes.length === 0 ? (
          <span style={{ fontSize: 11.5, color: 'var(--fg-3, #9a9aa2)' }}>None detected.</span>
        ) : (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }} aria-label="Detected runtimes">
            {runtimes.map((runtime) => (
              <span key={runtime} style={chipStyle()}>
                {runtime}
              </span>
            ))}
          </div>
        )}
      </div>

      <details
        open={showModels}
        onToggle={(event) => setShowModels((event.currentTarget as HTMLDetailsElement).open)}
      >
        <summary
          style={{
            cursor: 'pointer',
            fontSize: 11.5,
            color: showModels ? 'var(--accent, #5eb2ff)' : 'var(--fg-2, #d0d0d6)',
            listStyle: 'none',
          }}
        >
          Detected models ({models.length})
        </summary>
        {models.length === 0 ? (
          <p style={{ margin: '6px 0 0', fontSize: 11.5, color: 'var(--fg-3, #9a9aa2)' }}>
            No local models detected. Install Ollama / LM Studio to populate this list.
          </p>
        ) : (
          <ul
            style={{
              margin: '6px 0 0',
              paddingLeft: 18,
              fontSize: 11.5,
              color: 'var(--fg-2, #d0d0d6)',
            }}
            aria-label="Detected models"
          >
            {models.map(([runtime, name, sizeLabel], idx) => (
              <li key={`${runtime}:${name}:${idx}`}>
                <span style={{ color: 'var(--fg-1, #f0f0f0)' }}>{name}</span>{' '}
                <span style={chipStyle()}>{runtime}</span>{' '}
                <span style={chipStyle()}>{sizeLabel}</span>
              </li>
            ))}
          </ul>
        )}
      </details>
    </section>
  )
}
