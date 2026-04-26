import { useEffect, useState } from 'react'

import type { BootstrapPayload } from '../types'

const DEFAULT_BOOTSTRAP: BootstrapPayload = { mode: 'daemon' }

export function useBootstrap(): BootstrapPayload {
  const [payload, setPayload] = useState<BootstrapPayload>(DEFAULT_BOOTSTRAP)

  useEffect(() => {
    let cancelled = false
    fetch('/bootstrap')
      .then((response) => (response.ok ? response.json() : DEFAULT_BOOTSTRAP))
      .then((next: BootstrapPayload) => {
        if (!cancelled) setPayload({ ...DEFAULT_BOOTSTRAP, ...next })
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [])

  return payload
}
