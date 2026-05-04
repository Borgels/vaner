import { useEffect, useMemo, useState } from 'react'

import { getLiveWork, liveWorkStreamPath, openEventSource } from './client'
import type { LiveWorkEvent, LiveWorkSnapshot } from '../types'

export interface LiveWorkSelection {
  entityType: 'prediction' | 'scenario' | 'work_product' | 'worker'
  entityId: string
}

export function useLiveWork(selection: LiveWorkSelection | null) {
  const [snapshot, setSnapshot] = useState<LiveWorkSnapshot | null>(null)
  const [live, setLive] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!selection) {
      setSnapshot(null)
      setLive(false)
      setError(null)
      return
    }
    let cancelled = false
    let source: EventSource | null = null
    setError(null)
    getLiveWork(selection.entityType, selection.entityId)
      .then((payload) => {
        if (!cancelled) setSnapshot(payload)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })

    source = openEventSource(liveWorkStreamPath(selection.entityType, selection.entityId))
    source.onopen = () => {
      if (!cancelled) setLive(true)
    }
    source.onerror = () => {
      if (!cancelled) setLive(false)
    }
    source.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as LiveWorkEvent
        if (!event.event_id) return
        setSnapshot((current) => {
          if (!current) {
            return {
              entity_type: selection.entityType,
              entity_id: selection.entityId,
              status: event.status,
              summary: event.summary,
              active: isActiveEvent(event),
              updated_at: event.ts,
              events: [event],
              prediction: null,
              worker: null,
              queue: null,
            }
          }
          if (current.events.some((item) => item.event_id === event.event_id)) {
            return current
          }
          return {
            ...current,
            status: event.status,
            summary: event.summary,
            active: isActiveEvent(event),
            updated_at: event.ts,
            events: [...current.events, event].slice(-120),
          }
        })
      } catch {
        // Ignore malformed frames.
      }
    }
    return () => {
      cancelled = true
      source?.close()
      setLive(false)
    }
  }, [selection])

  return useMemo(() => ({ snapshot, live, error }), [snapshot, live, error])
}

function isActiveEvent(event: LiveWorkEvent): boolean {
  return (
    ['queued', 'running', 'grounding', 'evidence_gathering', 'drafting'].includes(event.status) ||
    ['model', 'progress', 'prediction_precompute'].includes(event.stage)
  )
}
