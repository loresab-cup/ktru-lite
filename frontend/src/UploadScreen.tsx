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

  const send = async (file: File | undefined) => {
    if (!file || uploading) return
    const name = file.name.toLowerCase()
    if (!EXTENSIONS.some((ext) => name.endsWith(ext))) return setError(T.upload.badType)
    if (file.size > MAX_BYTES) return setError(T.upload.tooBig)
    setError(null)
    setUploading(file.name)
    try {
      const { job_id } = await api.upload(file, inactive)
      onJob(job_id)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : T.errors.network)
      setUploading(null)
    }
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setOver(false)
    void send(e.dataTransfer.files[0])
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
                <Checkbox checked={inactive} onChange={(e) => setInactive(e.target.checked)}>
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
              accept={EXTENSIONS.join(',')}
              onChange={(e) => {
                void send(e.target.files?.[0])
                e.target.value = ''
              }}
            />
          </div>
          <p className="lead">{T.upload.lead}</p>
        </section>
        <Stats jobs={jobs} onChoose={() => input.current?.click()} />
      </div>
      <Features />
    </>
  )
}
