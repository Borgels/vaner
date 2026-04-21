import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { SystemVitals } from './SystemVitals'
import type { CycleState, ModelState } from '../api/usePipelineEvents'

function makeModel(overrides: Partial<ModelState> = {}): ModelState {
  return {
    pending: overrides.pending ?? new Map(),
    lastLatencyMs: overrides.lastLatencyMs ?? null,
    lastModel: overrides.lastModel ?? null,
    recentLatencies: overrides.recentLatencies ?? [],
    totalRequests: overrides.totalRequests ?? 0,
    totalErrors: overrides.totalErrors ?? 0,
  }
}

function makeCycle(overrides: Partial<CycleState> = {}): CycleState {
  return {
    current: overrides.current ?? null,
    lastFinished: overrides.lastFinished ?? null,
    totalCycles: overrides.totalCycles ?? 0,
    artefactsWritten: overrides.artefactsWritten ?? 0,
  }
}

describe('SystemVitals', () => {
  it('shows "running" while a cycle is in flight', () => {
    render(
      <SystemVitals
        live
        mode="daemon"
        cycle={makeCycle({ current: { cycleId: 'c1', startedAt: Date.now() / 1000 } })}
        model={makeModel()}
        scenarioCount={3}
        pendingLlm={0}
      />,
    )
    expect(screen.getByText(/running/)).toBeInTheDocument()
  })

  it('shows model busy with a spinner when requests are pending', () => {
    render(
      <SystemVitals
        live
        mode="daemon"
        cycle={makeCycle()}
        model={makeModel({ lastModel: 'qwen2.5-coder' })}
        scenarioCount={3}
        pendingLlm={2}
      />,
    )
    expect(screen.getByText(/busy · 2/)).toBeInTheDocument()
  })

  it('renders an offline badge when SSE is disconnected', () => {
    render(
      <SystemVitals
        live={false}
        mode="daemon"
        cycle={makeCycle()}
        model={makeModel()}
        scenarioCount={0}
        pendingLlm={0}
      />,
    )
    expect(screen.getByText('offline')).toBeInTheDocument()
  })

  it('reports error count when LLM failures accumulate', () => {
    render(
      <SystemVitals
        live
        mode="daemon"
        cycle={makeCycle()}
        model={makeModel({ totalErrors: 3 })}
        scenarioCount={0}
        pendingLlm={0}
      />,
    )
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText(/llm errors/)).toBeInTheDocument()
  })
})
