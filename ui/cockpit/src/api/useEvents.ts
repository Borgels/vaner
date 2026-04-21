import { useEffect, useRef, useState } from 'react'

import { openEventSource } from './client'
import type { UIEvent } from '../types'

interface UseEventsOptions<TPayload = UIEvent> {
  path: string
  maxItems?: number
  enabled?: boolean
  parse?: (raw: string) => TPayload
  toEvent?: (payload: TPayload) => UIEvent | null
  onPayload?: (payload: TPayload) => void
}

export function useEvents<TPayload = UIEvent>({
  path,
  maxItems = 120,
  enabled = true,
  parse,
  toEvent,
  onPayload,
}: UseEventsOptions<TPayload>) {
  const [events, setEvents] = useState<UIEvent[]>([])
  const [live, setLive] = useState(false)
  const retryRef = useRef<number | undefined>(undefined)

  useEffect(() => {
    if (!enabled) {
      setLive(false)
      return
    }

    let source: EventSource | null = null
    let closed = false
    let attempt = 0

    const connect = () => {
      if (closed || document.visibilityState === 'hidden') {
        return
      }

      source = openEventSource(path)
      source.onopen = () => {
        attempt = 0
        setLive(true)
      }
      source.onerror = () => {
        setLive(false)
        source?.close()
        const nextDelay = Math.min(5000, 500 * 2 ** attempt)
        attempt += 1
        retryRef.current = window.setTimeout(connect, nextDelay)
      }
      source.onmessage = (event) => {
        try {
          const payload = parse ? parse(event.data) : (JSON.parse(event.data) as TPayload)
          onPayload?.(payload)
          const streamEvent = toEvent ? toEvent(payload) : (payload as UIEvent)
          if (streamEvent) {
            setEvents((prev) => [streamEvent, ...prev].slice(0, maxItems))
          }
        } catch {
          // Ignore malformed events.
        }
      }
    }

    const onVisibilityChange = () => {
      if (document.visibilityState === 'hidden') {
        setLive(false)
        source?.close()
        source = null
        if (retryRef.current) {
          window.clearTimeout(retryRef.current)
        }
        return
      }

      connect()
    }

    connect()
    document.addEventListener('visibilitychange', onVisibilityChange)

    return () => {
      closed = true
      setLive(false)
      document.removeEventListener('visibilitychange', onVisibilityChange)
      source?.close()
      if (retryRef.current) {
        window.clearTimeout(retryRef.current)
      }
    }
  }, [enabled, maxItems, onPayload, parse, path, toEvent])

  return { events, live, setEvents }
}
