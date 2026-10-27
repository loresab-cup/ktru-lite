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
  if (job.status === 'failed') {
    return (
      <span className="job-state failed">
        <TriangleAlert size={28} aria-hidden="true" />
        {T.jobStatus.failed}
      </span>
    )
  }
  if (percent === undefined) return <Clock size={28} className="job-mark" aria-hidden="true" />
  return (
    <span className="job-state" title={T.app.ringHint(percent)}>
      <PercentRing percent={percent} size={44} />
      <span>
        <span className="sr-only">{percent} % </span>
        {T.app.ringLabel}
      </span>
    </span>
  )
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

/** A file name that may wrap after "_" and before the extension, not in the middle of a word. */
const breakable = (name: string) => name.replace(/_/g, '_\u200b').replace(/\.(\w+)$/, '\u200b.$1')

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
 *  tile also gets a check box, to the right of the ring. */
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
        const state = job.status === 'queued' ? T.jobStatus.queued : T.jobs.processing(job.processed, job.total)
        return (
          <li key={job.id} className="tile-wrap">
            <Link
              className={`tile ${checked ? 'selected' : ''} ${busy ? 'busy' : ''}`}
              to={paths.job(job.id)}
              title={`${T.jobs.open}: ${job.filename}`}
            >
              <span className={`tile-head ${selected && !busy ? 'with-check' : ''}`}>
                <FileTile name={job.filename} />
                {!busy && <JobMark job={job} percent={percent} />}
              </span>
              <span className="tile-name">{breakable(job.filename)}</span>
              <span className="tile-meta num">
                {T.upload.positions(job.total)}, {busy ? state : formatDate(job.created_at)}
              </span>
              {busy && <span className="tile-date num">{formatDate(job.created_at)}</span>}
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
