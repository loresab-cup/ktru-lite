import { Checkbox } from 'antd'
import { Clock, FileSpreadsheet, FileText, TriangleAlert } from 'lucide-react'
import type { Job } from './api'
import { Link } from './Link'
import { paths } from './router'
import { isBusy } from './useJobs'
import { T, formatDate } from './texts'
import { PercentRing, Skeleton } from './ui'

/** Share of the rows a running specification has done. */
const doneShare = (job: Job) => (job.total ? Math.floor((100 * job.processed) / job.total) : 0)

/** The ring with the ready share, or a mark when there is none. A running one has no ring:
 *  its progress is the bar at the foot of the tile. */
function JobMark({ job, percent }: { job: Job; percent?: number }) {
  if (job.status === 'failed') return <TriangleAlert size={28} className="job-mark low" aria-hidden="true" />
  if (percent === undefined) return <Clock size={28} className="job-mark" aria-hidden="true" />
  return <PercentRing percent={percent} />
}

/** A thin bar at the foot of a running tile: it fills with the rows done and shimmers, so the
 *  wait is seen in the list as well as inside the specification. */
function TileProgress({ job }: { job: Job }) {
  const share = doneShare(job)
  return (
    <span
      className={`tile-progress ${job.processed === 0 ? 'wait' : ''}`}
      role="progressbar"
      aria-label={T.jobs.processingLabel}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={share}
    >
      <span style={{ width: job.processed === 0 ? '100%' : `${Math.max(share, 6)}%` }} />
    </span>
  )
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
        const busy = isBusy(job)
        const state = busy
          ? job.status === 'queued'
            ? T.jobStatus.queued
            : T.jobs.processing(job.processed, job.total)
          : percent !== undefined
            ? T.app.ready(percent)
            : (T.jobStatus[job.status] ?? job.status)
        return (
          <li key={job.id} className="tile-wrap">
            <Link
              className={`tile ${checked ? 'selected' : ''} ${busy ? 'busy' : ''}`}
              to={paths.job(job.id)}
              title={T.jobs.open}
            >
              <span className="tile-head">
                <FileTile name={job.filename} />
                {!selected && !busy && <JobMark job={job} percent={percent} />}
              </span>
              <span className="tile-name">{job.filename}</span>
              <span className="tile-meta num">
                {T.upload.positions(job.total)}, {selected || busy ? state : formatDate(job.created_at)}
              </span>
              {(selected || busy) && <span className="tile-date num">{formatDate(job.created_at)}</span>}
              {busy && <TileProgress job={job} />}
            </Link>
            {/* a running one cannot be deleted, so it gets no check box */}
            {selected && !busy && (
              <span className="tile-check">
                <Checkbox
                  checked={checked}
                  onChange={() => onToggle?.(job.id)}
                  aria-label={T.jobs.select(job.filename)}
                  title={T.jobs.select(job.filename)}
                />
              </span>
            )}
          </li>
        )
      })}
    </ul>
  )
}
