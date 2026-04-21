import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { MismatchBanner, SettingsDrawer, TopBar } from './chrome'
import { DEFAULT_COCKPIT_SETTINGS } from '../lib/constants'
import type { BackendSettings, ComputeSettings, LimitSettings, MCPSettings } from '../types'

const BACKEND: BackendSettings = {
  name: 'custom',
  base_url: 'http://127.0.0.1:11434/v1',
  model: 'qwen2.5-coder:7b',
  api_key_env: '',
  prefer_local: true,
  fallback_enabled: false,
  fallback_base_url: '',
  fallback_model: '',
  fallback_api_key_env: '',
  remote_budget_per_hour: 60,
}

const COMPUTE: ComputeSettings = {
  device: 'cpu',
  cpu_fraction: 0.2,
  gpu_memory_fraction: 0.5,
  idle_only: true,
  idle_cpu_threshold: 0.35,
  idle_gpu_threshold: 0.35,
  embedding_device: null,
  exploration_concurrency: 4,
  max_parallel_precompute: 1,
  max_cycle_seconds: 300,
  max_session_minutes: null,
}

const MCP: MCPSettings = { transport: 'stdio', http_host: '127.0.0.1', http_port: 8472 }
const LIMITS: LimitSettings = { max_age_seconds: 86400, max_context_tokens: 4096 }

describe('TopBar', () => {
  it('renders the cockpit mode label but no ask/search input', () => {
    render(
      <TopBar
        mode="daemon"
        running={true}
        onToggleRun={() => undefined}
        packageState={null}
        onOpenSettings={() => undefined}
        onOpenPalette={() => undefined}
      />,
    )

    expect(screen.getByText(/COCKPIT · DAEMON/)).toBeInTheDocument()
    // The search bar must have been removed from the top chrome.
    expect(screen.queryByPlaceholderText(/ask/i)).toBeNull()
  })
})

describe('SettingsDrawer', () => {
  it('posts a backend preset change via onSaveBackend', () => {
    const onSaveBackend = vi.fn().mockResolvedValue(undefined)
    render(
      <SettingsDrawer
        open={true}
        onClose={() => undefined}
        mode="daemon"
        backend={BACKEND}
        compute={COMPUTE}
        mcp={MCP}
        limits={LIMITS}
        cockpit={DEFAULT_COCKPIT_SETTINGS}
        presets={[
          { name: 'ollama', base_url: 'http://127.0.0.1:11434/v1', default_model: 'qwen2.5-coder:7b', api_key_env: '' },
          { name: 'openai', base_url: 'https://api.openai.com/v1', default_model: 'gpt-4o-mini', api_key_env: 'OPENAI_API_KEY' },
        ]}
        devices={[{ id: 'cpu', label: 'CPU', kind: 'cpu' }]}
        devicesWarning={null}
        gatewayEnabled={false}
        onSaveBackend={onSaveBackend}
        onSaveCompute={() => Promise.resolve()}
        onSaveMcp={() => Promise.resolve()}
        onSaveContext={() => Promise.resolve()}
        onPatchCockpit={() => undefined}
      />,
    )

    const presetSelect = screen.getAllByRole('combobox')[0]
    fireEvent.change(presetSelect, { target: { value: 'openai' } })
    expect(onSaveBackend).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'openai', base_url: 'https://api.openai.com/v1' }),
    )
  })

  it('renders a warning when the device probe fails', () => {
    render(
      <SettingsDrawer
        open={true}
        onClose={() => undefined}
        mode="daemon"
        backend={BACKEND}
        compute={COMPUTE}
        mcp={MCP}
        limits={LIMITS}
        cockpit={DEFAULT_COCKPIT_SETTINGS}
        presets={[]}
        devices={[]}
        devicesWarning="torch not installed"
        gatewayEnabled={false}
        onSaveBackend={() => Promise.resolve()}
        onSaveCompute={() => Promise.resolve()}
        onSaveMcp={() => Promise.resolve()}
        onSaveContext={() => Promise.resolve()}
        onPatchCockpit={() => undefined}
      />,
    )
    expect(screen.getByText(/torch not installed/)).toBeInTheDocument()
  })
})

describe('MismatchBanner', () => {
  it('calls onReload when the reload button is clicked', () => {
    const onReload = vi.fn()
    render(<MismatchBanner onReload={onReload} />)
    fireEvent.click(screen.getByRole('button', { name: /reload/i }))
    expect(onReload).toHaveBeenCalled()
  })
})
