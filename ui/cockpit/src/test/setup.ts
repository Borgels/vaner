import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// 0.8.6 WS10 — explicit cleanup hook. With `globals: false` in vitest 4,
// the auto-cleanup that @testing-library/react traditionally registers
// against the global `afterEach` is not wired up; we register it here so
// each test gets a fresh DOM and getByRole() does not collide across
// renders that accumulate inside the same test file.
afterEach(() => {
  cleanup()
})

class MockResizeObserver {
  observe(): void {
    // noop
  }
  unobserve(): void {
    // noop
  }
  disconnect(): void {
    // noop
  }
}

if (typeof globalThis.ResizeObserver === 'undefined') {
  ;(globalThis as unknown as { ResizeObserver: typeof MockResizeObserver }).ResizeObserver =
    MockResizeObserver
}
