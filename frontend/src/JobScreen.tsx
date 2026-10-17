import { AlertTriangle, Check, RotateCcw, Upload } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, type Job } from './api'
import { Results } from './Results'
import { T } from './texts'
import { Ring, Skeleton } from './ui'

/** What the shell shows for the page: the title in the top bar and the ring in the sidebar. */
export interface PageInfo {
  title?: string
  ring?: { percent: number; label: string }
}

/** Step states from the job counters: read -> extract -> search -> confidence. */
function stepStates(job: Job): ('done' | 'active' | 'wait')[] {
  if (job.status === 'done') return ['done', 'done', 'done', 'done']
  if (job.status === 'queued') return ['wait', 'wait', 'wait', 'wait']
  if (job.total === 0) return ['active', 'wait', 'wait', 'wait']
  if (job.processed === 0) return ['done', 'active', 'wait', 'wait']
  return ['done', 'done', 'active', 'active']
}

/** Share of positions whose code needs no more work: auto, corrected and accepted. */
function readyPercent(job: Job): number {
  const c = job.counts
  if (!c || !job.total) return 0
  return Math.round((100 * (c.auto + c.corrected + c.accepted)) / job.total)
}

function ProgressCard({ job }: { job: Job }) {
  const percent = job.total ? Math.floor((100 * job.processed) / job.total) : 0
  const states = stepStates(job)
  return (
    <section className="progress card" aria-live="polite" aria-labelledby="progress-title">
      <Ring percent={percent} label={T.progress.ringLabel} />
      <div>
        <h2 id="progress-title" className="panel-title">
          {T.progress.title}
        </h2>
        <div className="cell-sub">{job.filename}</div>
        <ol className="steps">
          {T.progress.steps.map((step, i) => (
            <li key={step} className={states[i]}>
              <span className="step-dot" aria-hidden="true">
                {states[i] === 'done' && <Check size={13} strokeWidth={3} />}
              </span>
              {step}
            </li>
          ))}
        </ol>
        <div className="counter num">
          {job.status === 'queued' ? (
            T.progress.queued
          ) : (
            <>
              {T.progress.processed} <b>{job.processed}</b> {T.progress.of} <b>{job.total}</b>
            </>
          )}
        </div>
      </div>
    </section>
  )
}

function Problem({ title, text, onRetry, onHome }: {
  title: string
  text: string
  onRetry?: () => void
  onHome: () => void
}) {
  return (
    <section className="progress card" role="alert">
      <span className="round" style={{ width: 96, height: 96 }} aria-hidden="true">
        <AlertTriangle size={40} />
      </span>
      <div>
        <h2 className="panel-title">{title}</h2>
        <p className="summary" style={{ fontSize: 15 }}>
          {text}
        </p>
        <div className="toolbar" style={{ margin: 0 }}>
          {onRetry && (
            <button type="button" className="btn primary" onClick={onRetry}>
              <RotateCcw size={16} aria-hidden="true" /> {T.progress.retry}
            </button>
          )}
          <button type="button" className={onRetry ? 'btn' : 'btn primary'} onClick={onHome}>
            <Upload size={16} aria-hidden="true" /> {T.progress.another}
          </button>
        </div>
      </div>
    </section>
  )
}

export function JobScreen({ jobId, onHome, onInfo }: {
  jobId: string
  onHome: () => void
  onInfo: (info: PageInfo) => void
}) {
  const [job, setJob] = useState<Job | null>(null)
  const [lost, setLost] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let alive = true
    let timer = 0
    const poll = async () => {
      try {
        const next = await api.job(jobId)
        if (!alive) return
        setJob(next)
        setLost(null)
        if (next.status === 'queued' || next.status === 'processing') {
          timer = window.setTimeout(poll, 1000)
        }
      } catch (e) {
        if (!alive) return
        const own = e instanceof ApiError && e.message !== T.errors.network
        setLost(own ? e.message : T.progress.lost)
      }
    }
    void poll()
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [jobId, attempt])

  useEffect(() => {
    if (!job) return
    const ring = job.status === 'done' ? { percent: readyPercent(job), label: T.app.ringLabel } : undefined
    onInfo({ title: job.filename, ring })
  }, [job, onInfo])

  const refresh = useCallback(() => {
    api.job(jobId).then(setJob).catch(() => undefined)
  }, [jobId])

  if (lost) {
    return (
      <Problem
        title={T.errors.network}
        text={lost}
        onRetry={() => setAttempt((a) => a + 1)}
        onHome={onHome}
      />
    )
  }
  if (!job) {
    return (
      <section className="progress card" aria-busy="true">
        <Skeleton height={160} width={160} style={{ borderRadius: '50%' }} />
        <div>
          <Skeleton height={24} width="60%" />
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} height={18} style={{ marginTop: 16 }} />
          ))}
        </div>
      </section>
    )
  }
  if (job.status === 'failed') {
    return <Problem title={T.progress.failed} text={job.error ?? ''} onHome={onHome} />
  }
  if (job.status === 'done') return <Results job={job} onChanged={refresh} />
  return <ProgressCard job={job} />
}
