import { useCallback, useEffect, useState } from 'react'
import { api, readyPercent, type Job } from './api'

/** A job that is queued or running cannot be deleted: it has to be stopped first. */
export const isBusy = (job: Job) => job.status === 'queued' || job.status === 'processing'

/** Ready share of each finished job, from the counts the list already carries. */
function percentsOf(list: Job[]): Record<string, number> {
  const finished = list.filter((job) => job.status === 'done' || job.status === 'cancelled')
  return Object.fromEntries(finished.map((job) => [job.id, readyPercent(job)]))
}

/** Recent jobs with their ready share; refreshed every 3 s while one of them still runs. */
export function useJobs(limit: number) {
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [version, setVersion] = useState(0)
  useEffect(() => {
    let alive = true
    let timer = 0
    const load = () =>
      api
        .jobs(limit)
        .then((list) => {
          if (!alive) return
          setJobs(list)
          if (list.some(isBusy)) timer = window.setTimeout(load, 3000)
        })
        .catch(() => alive && setJobs([]))
    load()
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [limit, version])
  const reload = useCallback(() => setVersion((v) => v + 1), [])
  const percents = jobs ? percentsOf(jobs) : {}
  return { jobs, percents, reload }
}
