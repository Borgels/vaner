import { useEffect, useMemo, useState } from 'react'

import { adaptScenario } from './adapt'
import type { PipelineEvent } from './usePipelineEvents'
import type { ScenarioApiPayload, UIScenario } from '../types'

export function useScenarios(topK: number, events: PipelineEvent[]) {
  const [scenarioMap, setScenarioMap] = useState<Record<string, ScenarioApiPayload>>({})
  const [scenarios, setScenarios] = useState<UIScenario[]>([])

  useEffect(() => {
    let cancelled = false
    fetch(`/scenarios?limit=${encodeURIComponent(String(topK))}`)
      .then((response) => (response.ok ? response.json() : { scenarios: [] }))
      .then((payload: { scenarios?: ScenarioApiPayload[] }) => {
        if (cancelled) return
        const rows = payload.scenarios ?? []
        setScenarioMap(Object.fromEntries(rows.map((row) => [row.id, row])))
        setScenarios(rows.map(adaptScenario))
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [topK, events.length])

  return useMemo(() => ({ scenarios, setScenarios, scenarioMap, setScenarioMap }), [scenarioMap, scenarios])
}
