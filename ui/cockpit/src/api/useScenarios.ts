import { useEffect, useMemo, useState } from 'react'

import { adaptScenario } from './adapt'
import type { PipelineEvent } from './usePipelineEvents'
import type { ScenarioApiPayload, UIScenario } from '../types'

export function useScenarios(topK: number, events: PipelineEvent[], visibility: 'live' | 'history' | 'all' = 'live') {
  const [scenarioMap, setScenarioMap] = useState<Record<string, ScenarioApiPayload>>({})
  const [scenarios, setScenarios] = useState<UIScenario[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetch(`/scenarios?limit=${encodeURIComponent(String(topK))}&visibility=${encodeURIComponent(visibility)}`)
      .then(async (response) => {
        if (!response.ok) {
          throw new Error((await response.text().catch(() => '')) || `HTTP ${response.status}`)
        }
        return response.json()
      })
      .then((payload: { scenarios?: ScenarioApiPayload[] }) => {
        if (cancelled) return
        const rows = payload.scenarios ?? []
        setScenarioMap(Object.fromEntries(rows.map((row) => [row.id, row])))
        setScenarios(rows.map(adaptScenario))
        setError(null)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [topK, events.length, visibility])

  return useMemo(
    () => ({ scenarios, setScenarios, scenarioMap, setScenarioMap, loading, error }),
    [error, loading, scenarioMap, scenarios],
  )
}
