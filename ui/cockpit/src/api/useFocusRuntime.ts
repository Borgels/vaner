import { useCallback, useEffect, useState } from 'react'

import { getFocusRoute, getJobsStatus, getRecentActivity } from './client'
import type { FocusRoutePayload, JobsStatusPayload, RecentActivityPayload } from '../types'

export interface FocusRuntimeState {
  route: FocusRoutePayload | null
  jobs: JobsStatusPayload | null
  activity: RecentActivityPayload | null
  loading: boolean
  error: string | null
  refresh: () => Promise<void>
}

export function useFocusRuntime(intervalMs = 4000): FocusRuntimeState {
  const [route, setRoute] = useState<FocusRoutePayload | null>(null)
  const [jobs, setJobs] = useState<JobsStatusPayload | null>(null)
  const [activity, setActivity] = useState<RecentActivityPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    const jobsPromise = getJobsStatus()
      .then(setJobs)
      .catch(() => undefined)
    const activityPromise = getRecentActivity({ hostApp: 'codex-cli', limit: 12 })
      .then(setActivity)
      .catch(() => undefined)

    try {
      const routePayload = await getFocusRoute()
      setRoute(routePayload)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
    void jobsPromise
    void activityPromise
  }, [])

  useEffect(() => {
    void refresh()
    const interval = window.setInterval(() => {
      void refresh()
    }, intervalMs)
    return () => window.clearInterval(interval)
  }, [intervalMs, refresh])

  return { route, jobs, activity, loading, error, refresh }
}
