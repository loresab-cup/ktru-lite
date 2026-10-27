import { useCallback, useEffect, useState } from 'react'
import { api, readyPercent, type Job, type JobSort } from './api'

/** A job that is queued or running cannot be deleted: it has to be stopped first. */
export const isBusy = (job: Job) => job.status === 'queued' || job.status === 'processing'

/** Ready share of each finished job, from the counts the list already carries. */
function percentsOf(list: Job[]): Record<string, number> {
  const finished = list.filter((job) => job.status === 'done' || job.status === 'cancelled')
  return Object.fromEntries(finished.map((job) => [job.id, readyPercent(job)]))
}

/** Recent jobs with their ready share; refreshed every 3 s while one of them still runs.
 *  `q` keeps only the files whose name has it, `sort` orders them (newest first by default). */
export function useJobs(limit: number, q = '', sort: JobSort = 'new') {
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [total, setTotal] = useState(0)
  const [error, setError] = useState(false)
  const [version, setVersion] = useState(0)
  useEffect(() => {
    let alive = true
    let timer = 0
    const load = () =>
      api
        .jobs(limit, q, sort)
        .then(({ jobs: list, total: all }) => {
          if (!alive) return
          setJobs(list)
          setError(false)
          setTotal(all)
          if (list.some(isBusy)) timer = window.setTimeout(load, 3000)
        })
        .catch(() => {
          if (alive) setError(true)
        })
    load()
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [limit, q, sort, version])
  const reload = useCallback(() => {
    setError(false)
    setJobs(null)
    setVersion((v) => v + 1)
  }, [])
  const percents = jobs ? percentsOf(jobs) : {}
  return { jobs, total, percents, reload, error }
}
