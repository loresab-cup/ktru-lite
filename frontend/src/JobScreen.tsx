import { AlertTriangle, Check, RotateCcw, Upload } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, type Job } from './api'
import { Results } from './Results'
import { T } from './texts'
import { Skeleton } from './ui'

const RADIUS = 80
const CIRCLE = 2 * Math.PI * RADIUS

function Ring({ percent }: { percent: number }) {
  return (
    <svg width="200" height="200" viewBox="0 0 200 200" role="img" aria-label={`${percent} %`}>
      <circle className="ring-track" cx="100" cy="100" r={RADIUS} fill="none" strokeWidth="12" />
      <circle
        className="ring-value"
        cx="100"
        cy="100"
        r={RADIUS}
        fill="none"
        strokeWidth="12"
        strokeLinecap="round"
        strokeDasharray={CIRCLE}
        strokeDashoffset={CIRCLE * (1 - percent / 100)}
        transform="rotate(-90 100 100)"
      />
      <text className="ring-text num" x="100" y="104" textAnchor="middle">
        {percent} %
      </text>
      <text className="ring-sub" x="100" y="128" textAnchor="middle">
        {T.progress.ringLabel}
      </text>
    </svg>
  )
}

/** Step states from the job counters: read -> extract -> search -> confidence. */
function stepStates(job: Job): ('done' | 'active' | 'wait')[] {
  if (job.status === 'done') return ['done', 'done', 'done', 'done']
  if (job.status === 'queued') return ['wait', 'wait', 'wait', 'wait']
  if (job.total === 0) return ['active', 'wait', 'wait', 'wait']
  if (job.processed === 0) return ['done', 'active', 'wait', 'wait']
  return ['done', 'done', 'active', 'active']
}

function ProgressCard({ job }: { job: Job }) {
  const percent = job.total ? Math.floor((100 * job.processed) / job.total) : 0
  const states = stepStates(job)
  return (
    <section className="progress glass" aria-live="polite" aria-labelledby="progress-title">
      <Ring percent={percent} />
      <div>
        <h1 id="progress-title" className="panel-title" style={{ fontSize: 22 }}>
          {T.progress.title}
        </h1>
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
    <section className="progress glass" role="alert">
      <span className="round" style={{ width: 96, height: 96 }} aria-hidden="true">
        <AlertTriangle size={40} />
      </span>
      <div>
        <h1 className="panel-title" style={{ fontSize: 22 }}>
          {title}
        </h1>
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

export function JobScreen({ jobId, onHome }: { jobId: string; onHome: () => void }) {
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
        if (alive) setLost(e instanceof ApiError && e.message !== T.errors.network ? e.message : T.progress.lost)
      }
    }
    void poll()
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [jobId, attempt])

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
      <section className="progress glass" aria-busy="true">
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
    return (
      <Problem
        title={T.progress.failed}
        text={job.error ?? ''}
        onHome={onHome}
      />
    )
  }
  if (job.status === 'done') return <Results job={job} onChanged={refresh} onHome={onHome} />
  return <ProgressCard job={job} />
}
