import { Checkbox } from 'antd'
import {
  ArrowRight,
  Download,
  FilePlus2,
  FileSpreadsheet,
  FileText,
  FileUp,
  Play,
  Plus,
  Search,
  TriangleAlert,
  Upload,
  UploadCloud,
  X,
} from 'lucide-react'
import { useRef, useState, type DragEvent } from 'react'
import { api, ApiError } from './api'
import { CatalogPanel } from './CatalogPanel'
import { useHealth } from './health'
import { JobCards } from './JobCards'
import { Link } from './Link'
import { navigate, paths } from './router'
import { T } from './texts'
import { Tutorial } from './Tutorial'
import { Notice } from './ui'
import { useJobs } from './useJobs'

const EXTENSIONS = ['.xlsx', '.csv', '.docx', '.pdf', '.txt']
const MAX_BYTES = 10 * 1024 * 1024
/** Eight real-looking rows: the example of the tour and the template to download at once. */
const EXAMPLE_URL = '/primer.xlsx'
const XLSX_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

function FileList({ files, busy, onRemove, onClear, onProcess }: {
  files: File[]
  busy: string | null
  onRemove: (file: File) => void
  onClear: () => void
  onProcess: () => void
}) {
  if (!files.length) return null
  return (
    <section className="file-list" aria-labelledby="files-title">
      <div className="file-list-head">
        <h2 id="files-title" className="section-h">
          {T.upload.files}
        </h2>
        <button type="button" className="btn ghost small" onClick={onClear} disabled={Boolean(busy)}>
          {T.upload.clear}
        </button>
      </div>
      <ul>
        {files.map((file) => (
          <li key={`${file.name}-${file.size}`} className={busy === file.name ? 'busy' : ''}>
            <span className="file-tile" aria-hidden="true">
              {file.name.toLowerCase().endsWith('.xlsx') ? <FileSpreadsheet size={18} /> : <FileText size={18} />}
            </span>
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
              <X size={18} aria-hidden="true" />
            </button>
          </li>
        ))}
      </ul>
      <div className="file-actions">
        <button type="button" className="btn primary" onClick={onProcess} disabled={Boolean(busy)}>
          <Play size={16} fill="currentColor" aria-hidden="true" />
          {busy ? T.upload.uploading(busy) : T.upload.process(files.length)}
        </button>
      </div>
    </section>
  )
}

/** What the service says about itself before a file is sent: a loading catalog, no neural network. */
function ServiceNotices() {
  const health = useHealth()
  if (!health || health === 'offline') return null
  return (
    <>
      {health.catalog === 'loading' && (
        <Notice kind="info" title={T.app.banner.catalogLoading}>
          {T.app.banner.catalogLoadingHint}
        </Notice>
      )}
      {health.llm === 'off' && (
        <Notice kind="warn" title={T.app.banner.llmOff}>
          {T.app.banner.llmOffHint}
        </Notice>
      )}
      {health.embeddings === 'down' && (
        <Notice kind="warn" title={T.app.banner.embeddingsDown}>
          {T.app.banner.embeddingsDownHint}
        </Notice>
      )}
      {health.llm === 'down' && (
        <Notice kind="warn" title={T.app.banner.llmDown}>
          {T.app.banner.llmDownHint}
        </Notice>
      )}
    </>
  )
}

export function UploadScreen({ mode }: { mode: 'upload' | 'search' }) {
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const [inactive, setInactive] = useState(false)
  const [uploading, setUploading] = useState<string | null>(null)
  const [files, setFiles] = useState<File[]>([])
  const [errors, setErrors] = useState<string[]>([])
  const [sent, setSent] = useState(0)
  const { jobs, percents } = useJobs(8)
  const searching = mode === 'search'

  /** Files go to the list first; nothing is sent until "Process" is pressed. */
  const add = (list: FileList | File[] | null | undefined) => {
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

  /** The example file of the tour, put into the list as if the person had chosen it. */
  const addExample = async () => {
    try {
      const response = await fetch(EXAMPLE_URL)
      if (!response.ok) throw new Error(String(response.status))
      add([new File([await response.blob()], T.upload.exampleName, { type: XLSX_TYPE })])
    } catch {
      setErrors([T.upload.fileError(T.upload.exampleName, T.errors.network)])
    }
  }

  /** One specification per file; the first one opens, the others stay in the list. */
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

  const tabs = [
    { to: paths.home, label: T.upload.tabs.upload, icon: Upload, on: !searching },
    { to: paths.catalog, label: T.upload.tabs.search, icon: Search, on: searching },
  ]

  return (
    <>
      <section className="band" aria-labelledby="hero-title">
        <div className="wrap">
          <h1 id="hero-title" className="page-title">
            {searching ? T.catalogSearch.title : T.upload.title}
          </h1>
          <p className="lead">{searching ? T.catalogSearch.lead : T.upload.lead}</p>
          {!searching && <Tutorial onTry={() => void addExample()} />}
          {!searching && <ServiceNotices />}
          <div className="card home-card">
            <nav className="seg" aria-label={T.upload.tabsLabel}>
              {tabs.map(({ to, label, icon: Icon, on }) => (
                <Link key={to} to={to} aria-current={on ? 'page' : undefined}>
                  <Icon size={18} aria-hidden="true" /> {label}
                </Link>
              ))}
            </nav>

            {searching ? (
              <CatalogPanel />
            ) : (
              <>
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
                    {over ? <FileUp size={34} strokeWidth={1.5} /> : <UploadCloud size={34} strokeWidth={1.5} />}
                  </span>
                  <div className="drop-text">
                    <div className="drop-title">
                      {uploading
                        ? T.upload.uploading(uploading)
                        : over
                          ? T.upload.dropActive
                          : T.upload.dropTitle}
                    </div>
                    <div className="drop-hint">{T.upload.dropHint}</div>
                  </div>
                  {!over && (
                    <span className="btn primary" aria-hidden="true">
                      <Plus size={16} /> {T.upload.choose}
                    </span>
                  )}
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
                  <span className="grow" />
                  <button
                    type="button"
                    className="btn ghost small"
                    title={T.upload.exampleHint}
                    disabled={Boolean(uploading)}
                    onClick={() => void addExample()}
                  >
                    <FilePlus2 size={16} aria-hidden="true" /> {T.upload.example}
                  </button>
                  <a
                    className="btn ghost small"
                    href={EXAMPLE_URL}
                    download={T.upload.exampleName}
                    title={T.upload.templateHint}
                  >
                    <Download size={16} aria-hidden="true" /> {T.upload.template}
                  </a>
                </div>
                {errors.map((error) => (
                  <div key={error} className="notice error" role="alert">
                    <TriangleAlert size={22} aria-hidden="true" />
                    <div>
                      <b>{error}</b>
                    </div>
                  </div>
                ))}
                {sent > 0 && errors.length > 0 && (
                  <Notice kind="warn" title={T.upload.sent(sent)} />
                )}
                <FileList
                  files={files}
                  busy={uploading}
                  onRemove={(file) => setFiles(files.filter((f) => f !== file))}
                  onClear={() => setFiles([])}
                  onProcess={() => void process()}
                />
              </>
            )}
          </div>
        </div>
      </section>

      {!searching && (
        <section className="wrap section" aria-labelledby="recent-title">
          <div className="section-head">
            <h2 id="recent-title" className="section-h">
              {T.upload.recent}
            </h2>
            {jobs !== null && jobs.length > 0 && (
              <Link className="more" to={paths.jobs}>
                {T.upload.allJobs} <ArrowRight size={16} aria-hidden="true" />
              </Link>
            )}
          </div>
          <JobCards jobs={jobs} percents={percents} />
        </section>
      )}
    </>
  )
}
