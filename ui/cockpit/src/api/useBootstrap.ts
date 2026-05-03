import { useEffect, useState } from 'react'

import type { BootstrapPayload } from '../types'

export interface BootstrapState {
  payload: BootstrapPayload | null
  loading: boolean
  error: string | null
}

export function useBootstrap(): BootstrapState {
  const [state, setState] = useState<BootstrapState>({ payload: null, loading: true, error: null })

  useEffect(() => {
    let cancelled = false
    fetch('/bootstrap')
      .then(async (response) => {
        if (!response.ok) {
          throw new Error((await response.text().catch(() => '')) || `HTTP ${response.status}`)
        }
        return response.json()
      })
      .then((next: BootstrapPayload) => {
        if (!cancelled) setState({ payload: next, loading: false, error: null })
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setState({
            payload: null,
            loading: false,
            error: error instanceof Error ? error.message : String(error),
          })
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  return state
}
