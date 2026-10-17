import { Checkbox } from 'antd'
import {
  AlertTriangle,
  FileSearch,
  FileSpreadsheet,
  ScanSearch,
  Sparkles,
  UploadCloud,
  UserCheck,
} from 'lucide-react'
import { useEffect, useRef, useState, type DragEvent } from 'react'
import { api, ApiError, type Job } from './api'
import { Mountains } from './Mountains'
import { formatDate, T } from './texts'
import { EmptyState, JobStatusChip, Skeleton } from './ui'

const EXTENSIONS = ['.xlsx', '.csv', '.docx', '.pdf', '.txt']
const MAX_BYTES = 10 * 1024 * 1024

function RecentJobs({ onJob }: { onJob: (id: string) => void }) {
  const [jobs, setJobs] = useState<Job[] | null>(null)
  useEffect(() => {
    api.jobs().then(setJobs).catch(() => setJobs([]))
  }, [])

  return (
    <aside className="side glass" aria-labelledby="recent-title">
      <h2 id="recent-title">{T.upload.recent}</h2>
      {jobs === null &&
        [0, 1, 2].map((i) => <Skeleton key={i} height={64} style={{ marginBottom: 10 }} />)}
      {jobs?.length === 0 && (
        <EmptyState title={T.upload.recentEmpty} hint={T.upload.recentEmptyHint} />
      )}
      {jobs?.map((job) => (
        <button key={job.id} type="button" className="job-card" onClick={() => onJob(job.id)}>
          <div className="job-name" title={job.filename}>
            <FileSpreadsheet size={14} aria-hidden="true" style={{ verticalAlign: -2 }} />{' '}
            {job.filename}
          </div>
          <div className="job-meta num">
            <span>{formatDate(job.created_at)}</span>
            <span>{T.upload.positions(job.total)}</span>
            <JobStatusChip status={job.status} />
          </div>
        </button>
      ))}
    </aside>
  )
}

const HOW_ICONS = [FileSearch, Sparkles, ScanSearch, UserCheck]

function HowItWorks() {
  return (
    <section className="side glass" aria-labelledby="how-title">
      <h2 id="how-title">{T.upload.how}</h2>
      <ol className="how">
        {T.upload.howSteps.map((step, i) => {
          const Icon = HOW_ICONS[i]
          return (
            <li key={step}>
              <span className="tile-icon" aria-hidden="true">
                <Icon size={17} />
              </span>
              {step}
            </li>
          )
        })}
      </ol>
    </section>
  )
}

export function UploadScreen({ onJob }: { onJob: (id: string) => void }) {
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
    <div className="upload-grid">
      <section className="hero glass" aria-labelledby="hero-title">
        <Mountains className="hero-art" sky={false} />
        <div className="hero-top">
          <div className="eyebrow">{T.upload.eyebrow}</div>
          <h1 id="hero-title">{T.upload.title}</h1>
          <p>{T.upload.lead}</p>
        </div>
        <div>
          <div
            className={`drop glass ${over ? 'over' : ''}`}
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
              <UploadCloud size={32} strokeWidth={1.6} />
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
        </div>
      </section>
      <div className="side-stack">
        <RecentJobs onJob={onJob} />
        <HowItWorks />
      </div>
    </div>
  )
}
