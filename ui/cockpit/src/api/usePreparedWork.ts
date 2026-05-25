import { useCallback, useEffect, useMemo, useState } from 'react'

import { listPreparedWork } from './client'
import type { PreparedWorkCard } from '../types'

export function usePreparedWork(limit = 24, intervalMs = 4000) {
  const [cards, setCards] = useState<PreparedWorkCard[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const payload = await listPreparedWork({ limit, includeAdvisory: true, surface: 'cockpit' })
      setCards(payload.prepared_work ?? [])
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [limit])

  useEffect(() => {
    let cancelled = false
    const run = async () => {
      if (!cancelled) {
        await refresh()
      }
    }
    void run()
    const handle = window.setInterval(run, intervalMs)
    return () => {
      cancelled = true
      window.clearInterval(handle)
    }
  }, [intervalMs, refresh])

  return useMemo(
    () => ({ cards, setCards, loading, error, refresh }),
    [cards, error, loading, refresh],
  )
}
