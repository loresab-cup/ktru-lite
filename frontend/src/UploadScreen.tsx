import { Checkbox } from 'antd'
import {
  AlertTriangle,
  Clock,
  FileSearch,
  FileText,
  ListChecks,
  Play,
  ScanSearch,
  Sparkles,
  UploadCloud,
  X,
  type LucideIcon,
} from 'lucide-react'
import { useRef, useState, type DragEvent } from 'react'
import { api, ApiError } from './api'
import { JobCards } from './JobCards'
import { Link } from './Link'
import { navigate, paths } from './router'
import { useJobs } from './useJobs'
import { T } from './texts'

const EXTENSIONS = ['.xlsx', '.csv', '.docx', '.pdf', '.txt']
const MAX_BYTES = 10 * 1024 * 1024
const FEATURE_ICONS: LucideIcon[] = [FileSearch, Sparkles, ScanSearch, ListChecks]

/** How the service works: four steps, and how long it takes. */
function HowItWorks() {
  return (
    <section className="wrap section" aria-labelledby="how-title">
      <h2 id="how-title" className="section-h">
        {T.upload.how}
      </h2>
      <ol className="how-list">
        {T.upload.features.map(([title, text], i) => {
          const Icon = FEATURE_ICONS[i]
          return (
            <li key={title}>
              <span className="how-icon" aria-hidden="true">
                <Icon size={24} strokeWidth={1.5} />
              </span>
              <b>{title}</b>
              <span className="muted">{text}</span>
            </li>
          )
        })}
      </ol>
      <p className="how-speed muted">
        <Clock size={16} aria-hidden="true" /> {T.upload.speed}
      </p>
    </section>
  )
}

function FileList({ files, busy, onRemove, onClear, onProcess }: {
  files: File[]
  busy: string | null
  onRemove: (file: File) => void
  onClear: () => void
  onProcess: () => void
}) {
  if (!files.length) return <p className="file-empty">{T.upload.noFiles}</p>
  return (
    <section className="file-list" aria-labelledby="files-title">
      <div className="file-list-head">
        <h2 id="files-title">{T.upload.files}</h2>
        <button type="button" className="btn small" onClick={onClear} disabled={Boolean(busy)}>
          {T.upload.clear}
        </button>
      </div>
      <ul>
        {files.map((file) => (
          <li key={`${file.name}-${file.size}`} className={busy === file.name ? 'busy' : ''}>
            <FileText size={18} aria-hidden="true" />
            <span className="file-name">{file.name}</span>
            <span className="file-size num">{T.upload.size(file.size)}</span>
            <button
              type="button"
              className="icon-btn"
              aria-label={T.upload.remove(file.name)}
              title={T.upload.remove(file.name)}
              disabled={Boolean(busy)}
              onClick={() => onRemove(file)}
            >
              <X size={16} aria-hidden="true" />
            </button>
          </li>
        ))}
      </ul>
      <button type="button" className="btn primary process" onClick={onProcess} disabled={Boolean(busy)}>
        <Play size={16} aria-hidden="true" />
        {busy ? T.upload.uploading(busy) : T.upload.process(files.length)}
      </button>
    </section>
  )
}

export function UploadScreen() {
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const [inactive, setInactive] = useState(false)
  const [uploading, setUploading] = useState<string | null>(null)
  const [files, setFiles] = useState<File[]>([])
  const [errors, setErrors] = useState<string[]>([])
  const [sent, setSent] = useState(0)
  const { jobs, percents } = useJobs(8)

  /** Files go to the list first; nothing is sent until "Process" is pressed. */
  const add = (list: FileList | null | undefined) => {
    if (!list?.length || uploading) return
    const next = [...files]
    const bad: string[] = []
    for (const file of Array.from(list)) {
      const name = file.name.toLowerCase()
      if (!EXTENSIONS.some((ext) => name.endsWith(ext))) {
        bad.push(T.upload.fileError(file.name, T.upload.badType))
      } else if (file.size > MAX_BYTES) {
        bad.push(T.upload.fileError(file.name, T.upload.tooBig))
      } else if (!next.some((f) => f.name === file.name && f.size === file.size)) {
        next.push(file)
      }
    }
    setFiles(next)
    setErrors(bad)
    setSent(0)
  }

  /** One job per file; the first one opens, the others stay in the list of the jobs. */
  const process = async () => {
    if (!files.length || uploading) return
    const ids: string[] = []
    const bad: string[] = []
    const left: File[] = []
    for (const file of files) {
      setUploading(file.name)
      try {
        ids.push((await api.upload(file, inactive)).job_id)
      } catch (e) {
        bad.push(T.upload.fileError(file.name, e instanceof ApiError ? e.message : T.errors.network))
        left.push(file)
      }
    }
    setUploading(null)
    setFiles(left)
    setErrors(bad)
    setSent(ids.length)
    if (ids.length && !bad.length) navigate(paths.job(ids[0]))
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setOver(false)
    add(e.dataTransfer.files)
  }

  return (
    <>
      <section className="band" aria-labelledby="hero-title">
        <div className="wrap">
          <h1 id="hero-title" className="page-title">
            {T.upload.title}
          </h1>
          <p className="lead">{T.upload.lead}</p>
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
            <span className="drop-icon" aria-hidden="true">
              <UploadCloud size={34} strokeWidth={1.5} />
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
            </div>
            <input
              ref={input}
              type="file"
              hidden
              multiple
              accept={EXTENSIONS.join(',')}
              onChange={(e) => {
                add(e.target.files)
                e.target.value = ''
              }}
            />
          </div>
          <div className="drop-options">
            <Checkbox checked={inactive} onChange={(e) => setInactive(e.target.checked)}>
              {T.upload.inactive}
            </Checkbox>
          </div>
          {errors.map((error) => (
            <div key={error} className="alert" role="alert">
              <AlertTriangle size={18} aria-hidden="true" />
              {error}
            </div>
          ))}
          {sent > 0 && errors.length > 0 && (
            <div className="alert warn" role="status">
              {T.upload.sent(sent)}
            </div>
          )}
          <FileList
            files={files}
            busy={uploading}
            onRemove={(file) => setFiles(files.filter((f) => f !== file))}
            onClear={() => setFiles([])}
            onProcess={() => void process()}
          />
        </div>
      </section>

      <section className="wrap section" aria-labelledby="recent-title">
        <div className="section-head">
          <h2 id="recent-title" className="section-h">
            {T.upload.recent}
          </h2>
          {jobs !== null && jobs.length > 0 && (
            <Link className="more" to={paths.jobs}>
              {T.upload.allJobs}
            </Link>
          )}
        </div>
        <JobCards jobs={jobs} percents={percents} />
      </section>

      <HowItWorks />
    </>
  )
}
