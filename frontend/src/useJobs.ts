import { useEffect, useState } from 'react'
import { api, readyPercent, type Job } from './api'

/** Ready share of each finished job; the list itself has no counts, so each job is asked. */
async function loadPercents(list: Job[]): Promise<Record<string, number>> {
  const finished = list.filter((job) => job.status === 'done' || job.status === 'cancelled')
  const pairs = await Promise.all(
    finished.map((job) =>
      api
        .job(job.id)
        .then((full) => [job.id, readyPercent(full)] as const)
        .catch(() => null),
    ),
  )
  return Object.fromEntries(pairs.filter((pair) => pair !== null))
}

/** Recent jobs with their ready share; refreshed every 3 s while one of them still runs. */
export function useJobs(limit: number) {
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [percents, setPercents] = useState<Record<string, number>>({})
  useEffect(() => {
    let alive = true
    let timer = 0
    const load = () =>
      api
        .jobs(limit)
        .then((list) => {
          if (!alive) return
          setJobs(list)
          void loadPercents(list).then((next) => alive && setPercents(next))
          if (list.some((job) => job.status === 'queued' || job.status === 'processing')) {
            timer = window.setTimeout(load, 3000)
          }
        })
        .catch(() => alive && setJobs([]))
    load()
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [limit])
  return { jobs, percents }
}
