import { useCallback, useEffect, useMemo, useState } from 'react'

import { getActiveWork } from './client'
import type { ActiveWorkPayload } from '../types'

export function useActiveWork(intervalMs = 2500) {
  const [snapshot, setSnapshot] = useState<ActiveWorkPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const payload = await getActiveWork()
      setSnapshot(payload)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    const run = async () => {
      if (!cancelled) await refresh()
    }
    void run()
    const handle = window.setInterval(run, intervalMs)
    return () => {
      cancelled = true
      window.clearInterval(handle)
    }
  }, [intervalMs, refresh])

  return useMemo(() => ({ snapshot, loading, error, refresh }), [error, loading, refresh, snapshot])
}
