import { Checkbox } from 'antd'
import {
  AlertTriangle,
  FileSearch,
  ListChecks,
  ScanSearch,
  Sparkles,
  UploadCloud,
  type LucideIcon,
} from 'lucide-react'
import { useEffect, useRef, useState, type DragEvent } from 'react'
import { api, ApiError, type Health, type Job } from './api'
import { Sparkline, Waves } from './Art'
import { T } from './texts'
import { Skeleton } from './ui'

const EXTENSIONS = ['.xlsx', '.csv', '.docx', '.pdf', '.txt']
const MAX_BYTES = 10 * 1024 * 1024
const FEATURE_ICONS: LucideIcon[] = [FileSearch, Sparkles, ScanSearch, ListChecks]

function Features() {
  return (
    <section className="features card" aria-label={T.upload.features.map((f) => f[0]).join(', ')}>
      {T.upload.features.map(([title, text], i) => {
        const Icon = FEATURE_ICONS[i]
        return (
          <div key={title} className="feature">
            <Icon className="feature-icon" size={34} strokeWidth={1.4} aria-hidden="true" />
            <b>{title}</b>
            <span>{text}</span>
          </div>
        )
      })}
    </section>
  )
}

/** Real numbers only: catalog size from /health, jobs and positions from the recent jobs. */
function Stats({ jobs, onChoose }: { jobs: Job[] | null; onChoose: () => void }) {
  const [health, setHealth] = useState<Health | null>(null)
  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null))
  }, [])
  const done = (jobs ?? []).filter((job) => job.status === 'done')
  const totals = done.map((job) => job.total).reverse()
  const rows: [string, number | undefined, number[]][] = [
    [T.upload.statCodes, health?.codes, []],
    [T.upload.statJobs, jobs?.length, done.map((_, i) => i + 1)],
    [T.upload.statPositions, totals.reduce((sum, n) => sum + n, 0), totals],
  ]
  return (
    <aside className="side card" aria-labelledby="stats-title">
      <h2 id="stats-title" className="card-title">
        {T.upload.stats}
      </h2>
      {jobs === null
        ? [0, 1, 2].map((i) => <Skeleton key={i} height={30} style={{ margin: '10px 0' }} />)
        : rows.map(([label, value, series]) => (
            <div key={label} className="stat-row">
              <span>{label}</span>
              <span className="stat-value num">{(value ?? 0).toLocaleString('ru-RU')}</span>
              {series.length ? <Sparkline values={series} /> : <span />}
            </div>
          ))}
      <button type="button" className="btn primary" onClick={onChoose}>
        <UploadCloud size={16} aria-hidden="true" /> {T.upload.choose}
      </button>
    </aside>
  )
}

export function UploadScreen({ jobs, onJob }: {
  jobs: Job[] | null
  onJob: (id: string) => void
}) {
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const [inactive, setInactive] = useState(false)
  const [uploading, setUploading] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [files, setFiles] = useState<{ key: string; name: string; id?: string; status: string }[]>([])
  const [starting, setStarting] = useState(false)
  const busy = useRef(false)

  const send = async (selected: FileList | null) => {
    if (!selected?.length || busy.current) return
    busy.current = true
    setError(null)
    const entries = Array.from(selected).map((file) => ({ file, key: crypto.randomUUID() }))
    setFiles((old) => [...old, ...entries.map(({ file, key }) => ({ key, name: file.name, status: T.upload.waiting }))])
    for (const { file, key } of entries) {
      setUploading(file.name)
      let id: string | undefined
      let status = T.upload.ready
      try {
        const name = file.name.toLowerCase()
        if (!EXTENSIONS.some((ext) => name.endsWith(ext))) throw new ApiError(T.upload.badType)
        if (file.size > MAX_BYTES) throw new ApiError(T.upload.tooBig)
        setFiles((old) => old.map((f) => f.key === key ? { ...f, status: T.upload.uploading(file.name) } : f))
        id = (await api.upload(file, inactive)).job_id
      } catch (e) {
        status = T.upload.uploadError
        setError(e instanceof ApiError ? e.message : T.errors.network)
      }
      setFiles((old) => old.map((f) => f.key === key ? { ...f, id, status } : f))
    }
    setUploading(null)
    busy.current = false
  }

  const remove = async (key: string, id?: string) => {
    if (busy.current) return
    busy.current = true
    try {
      if (id) await api.remove(id)
      setFiles((old) => old.filter((f) => f.key !== key))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : T.errors.network)
    } finally {
      busy.current = false
    }
  }

  const start = async () => {
    if (busy.current) return
    busy.current = true
    setStarting(true)
    setError(null)
    const launched: string[] = []
    try {
      for (const file of files) {
        if (!file.id) continue
        await api.start(file.id)
        launched.push(file.id)
      }
      if (launched[0]) onJob(launched[0])
    } catch (e) {
      setError(e instanceof ApiError ? e.message : T.errors.network)
    } finally {
      setFiles((old) => old.filter((f) => !f.id || !launched.includes(f.id)))
      setStarting(false)
      busy.current = false
    }
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setOver(false)
    void send(e.dataTransfer.files)
  }

  return (
    <>
      <div className="upload-grid">
        <section className="hero card" aria-labelledby="hero-title">
          <div className="hero-chart">
            <Waves />
            <div className="hero-band num" aria-hidden="true">
              <small>{T.upload.bandLabel}</small>
              <b>{T.upload.bandValue}</b>
              <small>{T.upload.bandHint}</small>
            </div>
            <div className="hero-text">
              <div className="eyebrow">{T.upload.eyebrow}</div>
              <h2 id="hero-title">{T.upload.title}</h2>
            </div>
          </div>
          <div
            className={`drop ${over ? 'over' : ''}`}
            role="button"
            tabIndex={0}
            aria-label={T.upload.inputLabel}
            aria-busy={Boolean(uploading)}
            onClick={() => input.current?.click()}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                input.current?.click()
              }
            }}
            onDragOver={(e) => {
              e.preventDefault()
              setOver(true)
            }}
            onDragLeave={() => setOver(false)}
            onDrop={onDrop}
          >
            <span className="round drop-icon" aria-hidden="true">
              <UploadCloud size={30} strokeWidth={1.6} />
            </span>
            <div>
              <div className="drop-title">
                {uploading
                  ? T.upload.uploading(uploading)
                  : over
                    ? T.upload.dropActive
                    : T.upload.dropTitle}
              </div>
              <div className="drop-hint">{T.upload.dropHint}</div>
              <div
                className="drop-options"
                onClick={(e) => e.stopPropagation()}
                onKeyDown={(e) => e.stopPropagation()}
              >
                <Checkbox disabled={files.length > 0 || Boolean(uploading)} checked={inactive} onChange={(e) => setInactive(e.target.checked)}>
                  {T.upload.inactive}
                </Checkbox>
              </div>
              {error && (
                <div className="alert" role="alert">
                  <AlertTriangle size={18} aria-hidden="true" />
                  {error}
                </div>
              )}
            </div>
            <input
              ref={input}
              type="file"
              hidden
              multiple
              disabled={Boolean(uploading) || starting}
              accept={EXTENSIONS.join(',')}
              onChange={(e) => {
                void send(e.target.files)
                e.target.value = ''
              }}
            />
          </div>
          {files.length > 0 && <div className="upload-list" aria-live="polite">
            {files.map((file) => <div key={file.key} className="upload-file">
              <div><b>{file.name}</b><div className="cell-sub">{file.status}</div></div>
              <button type="button" className="btn" disabled={Boolean(uploading) || starting}
                aria-label={`${T.upload.remove}: ${file.name}`}
                onClick={() => void remove(file.key, file.id)}>{T.upload.remove}</button>
            </div>)}
            <button type="button" className="btn primary" disabled={Boolean(uploading) || starting || !files.some((f) => f.id)}
              onClick={() => void start()}>{starting ? T.upload.starting : T.upload.process}</button>
          </div>}
          <p className="lead">{T.upload.lead}</p>
        </section>
        <Stats jobs={jobs} onChoose={() => input.current?.click()} />
      </div>
      <Features />
    </>
  )
}
