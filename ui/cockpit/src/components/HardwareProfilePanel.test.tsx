import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { HardwareProfilePanel } from './HardwareProfilePanel'
import type { HardwareProfile } from '../types/setup'

function makeProfile(overrides: Partial<HardwareProfile> = {}): HardwareProfile {
  return {
    os: overrides.os ?? 'darwin',
    cpu_class: overrides.cpu_class ?? 'high',
    ram_gb: overrides.ram_gb ?? 32,
    gpu: overrides.gpu ?? 'apple_silicon',
    gpu_vram_gb: overrides.gpu_vram_gb ?? null,
    is_battery: overrides.is_battery ?? false,
    thermal_constrained: overrides.thermal_constrained ?? false,
    detected_runtimes: overrides.detected_runtimes ?? ['ollama', 'mlx'],
    detected_models:
      overrides.detected_models ?? [
        ['ollama', 'qwen2.5-coder:7b', '4.4GB'],
        ['ollama', 'llama3.1:8b', '4.7GB'],
      ],
    tier: overrides.tier ?? 'capable',
  }
}

describe('HardwareProfilePanel', () => {
  it('renders the unavailable fallback when profile is null', () => {
    render(<HardwareProfilePanel profile={null} />)
    expect(
      screen.getByText(/Hardware probe unavailable on this daemon/i),
    ).toBeInTheDocument()
  })

  it('renders the tier readout summary line', () => {
    render(<HardwareProfilePanel profile={makeProfile()} />)
    expect(screen.getByText(/Capable device/)).toBeInTheDocument()
    expect(screen.getByText(/32 GB RAM/)).toBeInTheDocument()
    expect(screen.getByText(/Apple Silicon GPU/)).toBeInTheDocument()
  })

  it('renders detected runtime chips', () => {
    render(<HardwareProfilePanel profile={makeProfile()} />)
    // "ollama" appears both as a runtime chip and inside each detected
    // model row, so query the runtime list by aria-label.
    const runtimeList = screen.getByLabelText('Detected runtimes')
    expect(runtimeList).toHaveTextContent('ollama')
    expect(runtimeList).toHaveTextContent('mlx')
  })

  it('renders detected models inside the disclosure', () => {
    render(<HardwareProfilePanel profile={makeProfile()} />)
    expect(screen.getByText(/Detected models \(2\)/)).toBeInTheDocument()
    expect(screen.getByText('qwen2.5-coder:7b')).toBeInTheDocument()
    expect(screen.getByText('llama3.1:8b')).toBeInTheDocument()
  })

  it('renders the empty-runtime hint when none detected', () => {
    render(
      <HardwareProfilePanel
        profile={makeProfile({ detected_runtimes: [], detected_models: [] })}
      />,
    )
    expect(screen.getByText(/None detected/i)).toBeInTheDocument()
    expect(
      screen.getByText(/No local models detected/i),
    ).toBeInTheDocument()
  })

  it('renders battery + thermal qualifiers when set', () => {
    render(
      <HardwareProfilePanel
        profile={makeProfile({ is_battery: true, thermal_constrained: true })}
      />,
    )
    expect(screen.getByText(/on battery/)).toBeInTheDocument()
    expect(screen.getByText(/thermal-constrained/)).toBeInTheDocument()
  })

  it('exposes a region role with the device heading', () => {
    render(<HardwareProfilePanel profile={makeProfile()} />)
    const region = screen.getByRole('region')
    expect(region).toBeInTheDocument()
    // The heading "DEVICE" labels the region.
    expect(screen.getByRole('heading', { name: 'DEVICE' })).toBeInTheDocument()
  })

  it('hides the refresh button when no onRefresh is provided', () => {
    render(<HardwareProfilePanel profile={makeProfile()} />)
    expect(
      screen.queryByRole('button', { name: /Re-run hardware probe/i }),
    ).not.toBeInTheDocument()
  })

  it('invokes onRefresh when the refresh button is clicked', () => {
    const onRefresh = vi.fn()
    render(
      <HardwareProfilePanel profile={makeProfile()} onRefresh={onRefresh} />,
    )
    fireEvent.click(screen.getByRole('button', { name: /Re-run hardware probe/i }))
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })

  it('disables the refresh button while refreshing', () => {
    render(
      <HardwareProfilePanel
        profile={makeProfile()}
        onRefresh={vi.fn()}
        refreshing
      />,
    )
    expect(
      screen.getByRole('button', { name: /Re-run hardware probe/i }),
    ).toBeDisabled()
  })
})
