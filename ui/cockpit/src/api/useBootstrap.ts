import { useEffect, useState } from 'react'

import { getBootstrap } from './client'
import type { BootstrapPayload, UIMode } from '../types'

function initialMode(): UIMode {
  return window.__VANER_MODE__ === 'proxy' ? 'proxy' : 'daemon'
}

export function useBootstrap() {
  const [bootstrap, setBootstrap] = useState<BootstrapPayload>({ mode: initialMode() })

  useEffect(() => {
    let cancelled = false

    getBootstrap()
      .then((payload) => {
        if (!cancelled) {
          setBootstrap(payload)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setBootstrap({ mode: initialMode() })
        }
      })

    return () => {
      cancelled = true
    }
  }, [])

  return bootstrap
}
