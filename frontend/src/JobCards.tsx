import { Checkbox } from 'antd'
import { Clock, FileSpreadsheet, FileText, Loader, TriangleAlert } from 'lucide-react'
import type { Job } from './api'
import { Link } from './Link'
import { paths } from './router'
import { isBusy } from './useJobs'
import { T, formatDate } from './texts'
import { PercentRing, Skeleton } from './ui'

/** The ring with the ready share; a running job spins, a failed one says so. */
function JobMark({ job, percent }: { job: Job; percent?: number }) {
  if (job.status === 'failed') return <TriangleAlert size={28} className="job-mark low" aria-hidden="true" />
  if (isBusy(job)) return <Loader size={28} className="job-mark spin" aria-hidden="true" />
  if (percent === undefined) return <Clock size={28} className="job-mark" aria-hidden="true" />
  return <PercentRing percent={percent} />
}

/** The file type tile: a table for xlsx, a sheet of text for the rest. */
function FileTile({ name }: { name: string }) {
  const Icon = name.toLowerCase().endsWith('.xlsx') ? FileSpreadsheet : FileText
  return (
    <span className="file-tile big" aria-hidden="true">
      <Icon size={22} />
    </span>
  )
}

/** Tiles of the specifications: a click opens the list of the codes. With `selected` every
 *  tile gets a check box in place of the ring. */
export function JobCards({ jobs, percents, selected, onToggle }: {
  jobs: Job[] | null
  percents: Record<string, number>
  selected?: Set<string>
  onToggle?: (id: string) => void
}) {
  if (jobs === null) {
    return (
      <div className="tiles">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} height={148} />
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
        const state = percent !== undefined ? T.app.ready(percent) : (T.jobStatus[job.status] ?? job.status)
        return (
          <li key={job.id} className="tile-wrap">
            <Link
              className={`tile ${checked ? 'selected' : ''}`}
              to={paths.job(job.id)}
              title={T.jobs.open}
            >
              <span className="tile-head">
                <FileTile name={job.filename} />
                {!selected && <JobMark job={job} percent={percent} />}
              </span>
              <span className="tile-name">{job.filename}</span>
              <span className="tile-meta num">
                {T.upload.positions(job.total)}, {selected ? state : formatDate(job.created_at)}
              </span>
              {selected && <span className="tile-date num">{formatDate(job.created_at)}</span>}
            </Link>
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
          </li>
        )
      })}
    </ul>
  )
}
