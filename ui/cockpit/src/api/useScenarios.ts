import { useEffect, useState } from 'react'

import { adaptScenario } from './adapt'
import { getScenario, listScenarios } from './client'
import type { ScenarioApiPayload, UIEvent, UIScenario } from '../types'

function mergeScenarioUpdate(previous: UIScenario, update: Partial<UIScenario>): UIScenario {
  return { ...previous, ...update }
}

export function useScenarios(topK: number, events: UIEvent[]) {
  const [scenarios, setScenarios] = useState<UIScenario[]>([])
  const [scenarioMap, setScenarioMap] = useState<Record<string, ScenarioApiPayload>>({})

  useEffect(() => {
    let cancelled = false

    listScenarios(topK)
      .then((payload) => {
        if (cancelled) {
          return
        }

        setScenarios(payload.scenarios.map(adaptScenario))
        setScenarioMap(Object.fromEntries(payload.scenarios.map((scenario) => [scenario.id, scenario])))
      })
      .catch(() => {
        if (!cancelled) {
          setScenarios([])
          setScenarioMap({})
        }
      })

    return () => {
      cancelled = true
    }
  }, [topK])

  useEffect(() => {
    if (!events.length) {
      return
    }

    const latestScenarioEvent = events.find((event) => event.scn)
    if (!latestScenarioEvent?.scn) {
      return
    }

    let cancelled = false

    getScenario(latestScenarioEvent.scn)
      .then((source) => {
        if (cancelled) {
          return
        }

        setScenarioMap((prev) => ({ ...prev, [source.id]: source }))
        const adapted = adaptScenario(source)
        setScenarios((prev) => {
          const existing = prev.find((item) => item.id === adapted.id)
          if (existing) {
            return prev.map((item) => (item.id === adapted.id ? mergeScenarioUpdate(item, adapted) : item))
          }
          return [adapted, ...prev]
        })
      })
      .catch(() => undefined)

    return () => {
      cancelled = true
    }
  }, [events])

  return { scenarios, setScenarios, scenarioMap, setScenarioMap }
}
