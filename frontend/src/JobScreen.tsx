import { App as AntApp, Dropdown, Tooltip } from 'antd'
import {
  ArrowLeft,
  Check,
  ChevronRight,
  Download,
  FileSpreadsheet,
  FileText,
  RotateCcw,
  Square,
  Trash2,
  TriangleAlert,
  Upload,
} from 'lucide-react'
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { api, ApiError, type Job } from './api'
import { Link } from './Link'
import { Results } from './Results'
import { navigate, paths } from './router'
import { T } from './texts'
import { EmptyState, Notice, Ring, Skeleton } from './ui'

/** Step states from the job counters: read -> extract -> search -> confidence. */
function stepStates(job: Job): ('done' | 'active' | 'wait')[] {
  if (job.status === 'done') return ['done', 'done', 'done', 'done']
  if (job.status === 'queued') return ['wait', 'wait', 'wait', 'wait']
  if (job.total === 0) return ['active', 'wait', 'wait', 'wait']
  if (job.processed === 0) return ['done', 'active', 'wait', 'wait']
  return ['done', 'done', 'active', 'active']
}

function StopButton({ job, onStopped }: { job: Job; onStopped: (job: Job) => void }) {
  const { modal } = AntApp.useApp()
  const [busy, setBusy] = useState(false)
  const stop = async () => {
    setBusy(true)
    try {
      onStopped(await api.cancel(job.id))
    } catch {
      setBusy(false) // the next poll shows the real state
    }
  }
  const ask = () =>
    modal.confirm({
      title: T.progress.stopTitle,
      content: T.progress.stopBody,
      icon: null,
      centered: true,
      okText: T.progress.stop,
      cancelText: T.progress.stopKeep,
      okButtonProps: { danger: true },
      onOk: stop,
    })
  return (
    <button type="button" className="btn danger" onClick={ask} disabled={busy}>
      <Square size={14} fill="currentColor" aria-hidden="true" /> {busy ? T.progress.stopping : T.progress.stop}
    </button>
  )
}

function ProgressCard({ job, onStopped }: { job: Job; onStopped: (job: Job) => void }) {
  const percent = job.total ? Math.floor((100 * job.processed) / job.total) : 0
  const states = stepStates(job)
  return (
    <section className="progress" aria-live="polite" aria-labelledby="progress-title">
      <Ring percent={percent} label={T.progress.ringLabel} spinning />
      <div className="progress-body">
        <h2 id="progress-title" className="section-h">
          {T.progress.title}
        </h2>
        <ol className="steps">
          {T.progress.steps.map((step, i) => (
            <li key={step} className={states[i]}>
              <span className="step-dot" aria-hidden="true">
                {states[i] === 'done' && <Check size={14} strokeWidth={3} />}
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
        {job.processed > 0 && (
          <p className="muted progress-note">
            {job.processed === job.total ? T.progress.choosing : T.progress.live}
          </p>
        )}
        <div className="actions">
          <StopButton job={job} onStopped={onStopped} />
        </div>
      </div>
    </section>
  )
}

interface LoadProblem {
  title: string
  text: string
  retry: boolean // a deleted job does not come back, so there is nothing to retry
}

/** What the person sees when the job cannot be loaded: no connection, no such job, a server error. */
function loadProblem(error: unknown): LoadProblem {
  if (error instanceof ApiError && error.status === 404) {
    return { title: T.job.missing, text: T.job.missingHint, retry: false }
  }
  if (error instanceof ApiError && error.status > 0) {
    return { title: T.progress.loadFailed, text: error.message, retry: true }
  }
  return { title: T.errors.network, text: T.progress.lost, retry: true }
}

function Problem({ title, text, onRetry, lost }: {
  title: string
  text: string
  onRetry?: () => void
  lost?: boolean // a missing page is a quiet blue picture, a failure a red one
}) {
  return (
    <EmptyState icon={TriangleAlert} tone={lost ? 'blue' : 'red'} title={title} hint={text} role="alert">
      {onRetry && (
        <button type="button" className="btn primary" onClick={onRetry}>
          <RotateCcw size={16} aria-hidden="true" /> {T.progress.retry}
        </button>
      )}
      <Link className={`btn ${onRetry ? '' : 'primary'}`} to={paths.jobs}>
        {T.job.toJobs}
      </Link>
      <Link className="btn" to={paths.home}>
        <Upload size={16} aria-hidden="true" /> {T.progress.another}
      </Link>
    </EmptyState>
  )
}

/** Round buttons fixed to the bottom of the window: export and delete. On a phone they are a bar. */
function Dock({ job, exportable }: { job: Job; exportable: boolean }) {
  const { message, modal } = AntApp.useApp()
  const remove = async () => {
    try {
      await api.deleteJob(job.id)
      navigate(paths.jobs)
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : T.errors.network)
    }
  }
  const ask = () =>
    modal.confirm({
      title: T.job.deleteTitle,
      content: T.job.deleteBody(job.filename, job.total),
      icon: null,
      centered: true,
      okText: T.job.deleteYes,
      cancelText: T.job.deleteNo,
      okButtonProps: { danger: true },
      onOk: remove,
    })
  const entry = (key: 'xlsx' | 'csv', icon: ReactNode, name: string, hint: string) => ({
    key,
    icon,
    label: (
      <span className="menu-entry">
        <b>{name}</b>
        <small>{hint}</small>
      </span>
    ),
  })
  return (
    <div className="dock" role="group" aria-label={T.dock.label}>
      {exportable && (
        <Tooltip title={T.dock.exportTitle} placement="left">
          <Dropdown
            trigger={['click']}
            placement="topRight"
            autoFocus // keyboard: the menu takes focus, arrows and Enter pick the format
            menu={{
              items: [
                entry('xlsx', <FileSpreadsheet size={20} />, T.dock.exportXlsx, T.dock.exportXlsxHint),
                entry('csv', <FileText size={20} />, T.dock.exportCsv, T.dock.exportCsvHint),
              ],
              // A menu item, not a link inside it: works with the mouse and with Enter alike;
              // the file is an attachment, so the page stays where it is
              onClick: ({ key }) => window.location.assign(api.exportUrl(job.id, key as 'xlsx' | 'csv')),
            }}
          >
            <button type="button" className="round primary" aria-label={T.dock.exportTitle}>
              <Download size={24} aria-hidden="true" />
              <span className="dock-label">{T.dock.export}</span>
            </button>
          </Dropdown>
        </Tooltip>
      )}
      <Tooltip title={T.job.delete} placement="left">
        <button type="button" className="round danger" aria-label={T.job.delete} onClick={ask}>
          <Trash2 size={24} aria-hidden="true" />
          <span className="dock-label">{T.dock.delete}</span>
        </button>
      </Tooltip>
    </div>
  )
}

export function JobScreen({ jobId }: { jobId: string }) {
  const [job, setJob] = useState<Job | null>(null)
  const [problem, setProblem] = useState<LoadProblem | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let alive = true
    let timer = 0
    const poll = async () => {
      try {
        const next = await api.job(jobId)
        if (!alive) return
        setJob(next)
        setProblem(null)
        if (next.status === 'queued' || next.status === 'processing') {
          timer = window.setTimeout(poll, 1000)
        }
      } catch (e) {
        if (!alive) return
        setProblem(loadProblem(e))
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

  const running = job?.status === 'queued' || job?.status === 'processing'
  const finished = job?.status === 'done' || job?.status === 'cancelled'

  const body = () => {
    if (problem) {
      const retry = () => {
        setProblem(null) // the skeleton shows that the request goes out again
        setAttempt((a) => a + 1)
      }
      return (
        <Problem
          title={problem.title}
          text={problem.text}
          onRetry={problem.retry ? retry : undefined}
          lost={!problem.retry}
        />
      )
    }
    if (!job) {
      return (
        <section className="progress" aria-busy="true">
          <Skeleton height={132} width={132} style={{ borderRadius: '50%' }} />
          <div style={{ flex: 1 }}>
            <Skeleton height={24} width="60%" />
            {[0, 1, 2].map((i) => (
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
          <Notice kind="warn" title={T.progress.cancelledTitle}>
            {T.progress.cancelled(job.processed, job.total)}
          </Notice>
          <Results job={job} onChanged={refresh} />
        </>
      )
    }
    // The card stays while the rows come in: the finished ones are listed under it
    return (
      <>
        <ProgressCard job={job} onStopped={setJob} />
        {job.processed > 0 && <Results job={job} onChanged={refresh} />}
      </>
    )
  }

  const subtitle = () => {
    if (!job) return null
    if (job.status === 'done') return T.results.took(job.stage_seconds.total ?? 0)
    if (job.status === 'cancelled') return T.progress.stoppedAt(job.processed, job.total)
    if (running) return T.progress.running
    return null
  }

  const showDock = Boolean(job) && !running && !problem
  return (
    <>
      <section className="page-head">
        <div className="wrap">
          <nav className="crumbs" aria-label={T.job.title}>
            <ArrowLeft size={16} aria-hidden="true" />
            <Link to={paths.jobs}>{T.job.backToJobs}</Link>
            {job && (
              <>
                <ChevronRight size={14} className="crumb-sep" aria-hidden="true" />
                <span className="crumb-now" title={job.filename}>
                  {job.filename}
                </span>
              </>
            )}
          </nav>
          <h1 className="page-title">
            {T.job.title}
            {job && <span className="title-count num">{T.results.positions(job.total)}</span>}
          </h1>
          {subtitle() && <p className="muted num took">{subtitle()}</p>}
        </div>
      </section>
      <div className={`wrap section stack ${showDock ? 'has-dock' : ''}`}>{body()}</div>
      {showDock && job && <Dock job={job} exportable={finished} />}
    </>
  )
}
