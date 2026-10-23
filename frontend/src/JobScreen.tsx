import { message, Popconfirm } from 'antd'
import { AlertTriangle, ArrowLeft, Check, Info, RotateCcw, Square, Trash2, Upload } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, type Job } from './api'
import { ExportButton, Results } from './Results'
import { Link } from './Link'
import { navigate, paths } from './router'
import { T } from './texts'
import { Ring, Skeleton } from './ui'

/** Step states from the job counters: read -> extract -> search -> confidence. */
function stepStates(job: Job): ('done' | 'active' | 'wait')[] {
  if (job.status === 'done') return ['done', 'done', 'done', 'done']
  if (job.status === 'queued') return ['wait', 'wait', 'wait', 'wait']
  if (job.total === 0) return ['active', 'wait', 'wait', 'wait']
  if (job.processed === 0) return ['done', 'active', 'wait', 'wait']
  return ['done', 'done', 'active', 'active']
}

function StopButton({ job, onStopped }: { job: Job; onStopped: (job: Job) => void }) {
  const [busy, setBusy] = useState(false)
  const stop = async () => {
    setBusy(true)
    try {
      onStopped(await api.cancel(job.id))
    } catch {
      setBusy(false) // the next poll shows the real state
    }
  }
  return (
    <button type="button" className="btn danger" onClick={stop} disabled={busy}>
      <Square size={14} aria-hidden="true" /> {busy ? T.progress.stopping : T.progress.stop}
    </button>
  )
}

/** Thin progress line above the live list: rows keep arriving while the user works. */
function LiveHead({ job, onStopped }: { job: Job; onStopped: (job: Job) => void }) {
  const percent = job.total ? Math.floor((100 * job.processed) / job.total) : 0
  return (
    <section className="live-head" aria-live="polite">
      <div className="counter num">
        {T.progress.processed} <b>{job.processed}</b> {T.progress.of} <b>{job.total}</b>
      </div>
      <div
        className="live-bar"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={T.progress.title}
      >
        <span style={{ width: `${percent}%` }} />
      </div>
      <StopButton job={job} onStopped={onStopped} />
      <p className="muted live-note">
        {job.total > 0 && job.processed === job.total ? T.progress.choosing : T.progress.live}
      </p>
    </section>
  )
}

function ProgressCard({ job, onStopped }: { job: Job; onStopped: (job: Job) => void }) {
  const percent = job.total ? Math.floor((100 * job.processed) / job.total) : 0
  const states = stepStates(job)
  return (
    <section className="progress" aria-live="polite" aria-labelledby="progress-title">
      <Ring percent={percent} label={T.progress.ringLabel} />
      <div>
        <h2 id="progress-title" className="section-h">
          {T.progress.title}
        </h2>
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
        <div className="actions">
          <StopButton job={job} onStopped={onStopped} />
        </div>
      </div>
    </section>
  )
}

function Problem({ title, text, onRetry }: { title: string; text: string; onRetry?: () => void }) {
  return (
    <section className="progress problem" role="alert">
      <span className="problem-icon" aria-hidden="true">
        <AlertTriangle size={40} />
      </span>
      <div>
        <h2 className="section-h">{title}</h2>
        <p className="summary">{text}</p>
        <div className="actions">
          {onRetry && (
            <button type="button" className="btn primary" onClick={onRetry}>
              <RotateCcw size={16} aria-hidden="true" /> {T.progress.retry}
            </button>
          )}
          <Link className={onRetry ? 'btn' : 'btn primary'} to={paths.home}>
            <Upload size={16} aria-hidden="true" /> {T.progress.another}
          </Link>
        </div>
      </div>
    </section>
  )
}

function DeleteButton({ job }: { job: Job }) {
  const [toast, holder] = message.useMessage()
  const [busy, setBusy] = useState(false)
  const remove = async () => {
    setBusy(true)
    try {
      await api.deleteJob(job.id)
      navigate(paths.jobs)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : T.errors.network)
      setBusy(false)
    }
  }
  return (
    <>
      {holder}
      <Popconfirm
        title={T.job.deleteAsk(job.filename)}
        okText={T.job.deleteYes}
        cancelText={T.job.deleteNo}
        okButtonProps={{ danger: true }}
        onConfirm={remove}
      >
        <button type="button" className="btn danger" disabled={busy}>
          <Trash2 size={16} aria-hidden="true" /> {T.job.delete}
        </button>
      </Popconfirm>
    </>
  )
}

export function JobScreen({ jobId }: { jobId: string }) {
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

  const refresh = useCallback(() => {
    api.job(jobId).then(setJob).catch(() => undefined)
  }, [jobId])

  const finished = job?.status === 'done' || job?.status === 'cancelled'
  const seconds = finished ? job?.stage_seconds.total : undefined

  const body = () => {
    if (lost) {
      return <Problem title={T.errors.network} text={lost} onRetry={() => setAttempt((a) => a + 1)} />
    }
    if (!job) {
      return (
        <section className="progress" aria-busy="true">
          <Skeleton height={160} width={160} style={{ borderRadius: '50%' }} />
          <div style={{ flex: 1 }}>
            <Skeleton height={24} width="60%" />
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} height={18} style={{ marginTop: 16 }} />
            ))}
          </div>
        </section>
      )
    }
    if (job.status === 'failed') return <Problem title={T.progress.failed} text={job.error ?? ''} />
    if (job.status === 'done') return <Results job={job} onChanged={refresh} />
    if (job.status === 'cancelled') {
      return (
        <>
          <div className="alert warn" role="status">
            <Info size={18} aria-hidden="true" style={{ flex: 'none' }} />
            {T.progress.cancelled(job.processed, job.total)}
          </div>
          <Results job={job} onChanged={refresh} />
        </>
      )
    }
    if (job.processed === 0) return <ProgressCard job={job} onStopped={setJob} />
    return (
      <>
        <LiveHead job={job} onStopped={setJob} />
        <Results job={job} onChanged={refresh} />
      </>
    )
  }

  return (
    <>
      <section className="band">
        <div className="wrap">
          <nav className="crumbs" aria-label={T.job.title}>
            <ArrowLeft size={16} aria-hidden="true" />
            <Link to={paths.jobs}>{T.job.backToJobs}</Link>
            {job && (
              <>
                <span className="crumb-sep" aria-hidden="true">
                  ›
                </span>
                <span className="crumb-now" title={job.filename}>
                  {job.filename}
                </span>
              </>
            )}
          </nav>
          <div className="title-row">
            <h1 className="page-title">
              {T.job.title}
              {job && (
                <span className="title-count num">{T.results.positions(job.total)}</span>
              )}
            </h1>
            {job && job.status !== 'queued' && job.status !== 'processing' && (
              <div className="actions top">
                {finished && <ExportButton jobId={job.id} />}
                <DeleteButton job={job} />
              </div>
            )}
          </div>
          {seconds !== undefined && <p className="muted num took">{T.results.took(seconds)}</p>}
        </div>
      </section>
      <div className="wrap section">{body()}</div>
    </>
  )
}
