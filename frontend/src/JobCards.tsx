import { Checkbox } from 'antd'
import { AlertTriangle, Clock, Loader } from 'lucide-react'
import type { Job } from './api'
import { Link } from './Link'
import { paths } from './router'
import { isBusy } from './useJobs'
import { T, formatDate } from './texts'
import { MiniRing, Skeleton } from './ui'

function JobMark({ job, percent }: { job: Job; percent?: number }) {
  if (job.status === 'failed') return <AlertTriangle size={30} className="job-mark low" aria-hidden="true" />
  if (job.status === 'queued' || job.status === 'processing') {
    return <Loader size={30} className="job-mark spin" aria-hidden="true" />
  }
  if (percent === undefined) return <Clock size={30} className="job-mark" aria-hidden="true" />
  return <MiniRing percent={percent} size={44} />
}

/** Bordered tiles of the jobs: a click opens the list of the codes of that job. */
export function JobCards({ jobs, percents, selected, onToggle }: {
  jobs: Job[] | null
  percents: Record<string, number>
  selected?: Set<string> // with it every tile gets a check box
  onToggle?: (id: string) => void
}) {
  if (jobs === null) {
    return (
      <div className="tiles">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} height={168} />
        ))}
      </div>
    )
  }
  if (!jobs.length) return <p className="muted">{T.upload.recentEmpty}</p>
  return (
    <ul className="tiles">
      {jobs.map((job) => {
        const percent = percents[job.id]
        const checked = selected?.has(job.id) ?? false
        return (
          <li key={job.id} className="tile-wrap">
            {selected && (
              <span className="tile-check">
                <Checkbox
                  checked={checked}
                  disabled={isBusy(job)}
                  onChange={() => onToggle?.(job.id)}
                  aria-label={T.jobs.select(job.filename)}
                  title={isBusy(job) ? T.jobs.busy : T.jobs.select(job.filename)}
                />
              </span>
            )}
            <Link
              className={`tile ${checked ? 'selected' : ''}`}
              to={paths.job(job.id)}
              title={T.jobs.open}
            >
              <JobMark job={job} percent={percent} />
              <span className="tile-name">{job.filename}</span>
              <span className="tile-meta num">
                {T.upload.positions(job.total)} ·{' '}
                {percent !== undefined ? T.app.ready(percent) : (T.jobStatus[job.status] ?? job.status)}
              </span>
              <span className="tile-date num">{formatDate(job.created_at)}</span>
            </Link>
          </li>
        )
      })}
    </ul>
  )
}
