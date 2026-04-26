import { useEffect, useState } from 'react'

import { openEventSource } from './client'
import type { UIEvent } from '../types'

interface UseEventsOptions<T> {
  path: string
  enabled?: boolean
  parse: (raw: string) => T
  toEvent: (payload: T) => UIEvent
  onPayload?: (payload: T) => void
}

export function useEvents<T>({ path, enabled = true, parse, toEvent, onPayload }: UseEventsOptions<T>) {
  const [events, setEvents] = useState<UIEvent[]>([])
  const [live, setLive] = useState(false)

  useEffect(() => {
    if (!enabled) {
      setLive(false)
      return
    }
    const source = openEventSource(path)
    source.onopen = () => setLive(true)
    source.onerror = () => setLive(false)
    source.onmessage = (message) => {
      try {
        const payload = parse(message.data)
        onPayload?.(payload)
        setEvents((current) => [toEvent(payload), ...current].slice(0, 200))
      } catch {
        // ignore malformed stream rows
      }
    }
    return () => source.close()
  }, [enabled, onPayload, parse, path, toEvent])

  return { events, setEvents, live }
}
