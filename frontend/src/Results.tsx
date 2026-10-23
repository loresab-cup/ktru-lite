import { Checkbox, Dropdown, Input, InputNumber, message, Pagination } from 'antd'
import {
  CheckCheck,
  ChevronDown,
  Download,
  Eye,
  FileSpreadsheet,
  FileText,
  Info,
  Search,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, ApiError, type ItemRow, type Job } from './api'
import { Link } from './Link'
import { navigate, paths } from './router'
import { T } from './texts'
import { Confidence, EmptyState, Skeleton, StatusChip, StatusMark } from './ui'

const PAGE_SIZE = 20
const FILTERS = ['auto', 'needs_review', 'corrected', 'accepted'] as const

export function ExportButton({ jobId }: { jobId: string }) {
  const items = [
    { key: 'xlsx', icon: <FileSpreadsheet size={16} />, label: T.results.exportXlsx },
    { key: 'csv', icon: <FileText size={16} />, label: T.results.exportCsv },
  ] as const
  return (
    <Dropdown
      trigger={['click']}
      placement="bottomRight"
      autoFocus // keyboard: the menu takes focus, arrows and Enter pick the format
      menu={{
        items: items.map(({ key, icon, label }) => ({ key, icon, label })),
        // A menu item, not a link inside it: works with the mouse and with Enter alike;
        // the file is an attachment, so the page stays where it is
        onClick: ({ key }) => window.location.assign(api.exportUrl(jobId, key as 'xlsx' | 'csv')),
      }}
    >
      <button type="button" className="btn primary">
        <Download size={16} aria-hidden="true" /> {T.results.export}
      </button>
    </Dropdown>
  )
}

function attributeLines(item: ItemRow): [string, string][] {
  const a = item.attributes
  const lines: [string, string | undefined][] = [
    [T.drawer.name, a.name],
    [T.drawer.brand, a.brand],
    [T.drawer.model, a.model],
  ]
  return lines.filter((line): line is [string, string] => Boolean(line[1]))
}

function ResultCard({ item, jobId, open, onToggle }: {
  item: ItemRow
  jobId: string
  open: boolean
  onToggle: () => void
}) {
  const to = paths.item(jobId, item.id)
  const lines = attributeLines(item)
  const characteristics = item.attributes.characteristics ?? []
  return (
    <article className="res-card">
      <div className="res-main">
        <div className="res-top">{T.results.fileRow(item.row_number)}</div>
        <div className="res-code-row">
          {item.ktru_code ? (
            <Link className="res-code" to={to}>
              {item.ktru_code}
            </Link>
          ) : (
            <Link className="res-code none" to={to}>
              {T.results.noCode}
            </Link>
          )}
          <StatusMark status={item.status} />
        </div>
        {item.ktru_name && (
          <Link className="res-name" to={to} tabIndex={-1}>
            {item.ktru_name}
          </Link>
        )}
        {lines.length > 0 && (
          <div className="res-meta">
            {lines.map(([label, value]) => (
              <span key={label} className="res-attr">
                <span className="res-meta-label">{label}:</span> {value}
              </span>
            ))}
          </div>
        )}
        {/* one line only: the whole text is under "Characteristics" */}
        {!open && <div className="res-preview">{item.raw_text}</div>}
      </div>
      <div className="res-side">
        <dl className="facts">
          <div>
            <dt>{T.results.confidence}</dt>
            <dd>
              <Confidence value={item.confidence} zone={item.zone} />
            </dd>
          </div>
          <div>
            <dt>{T.results.status}</dt>
            <dd>
              <StatusChip status={item.status} />
            </dd>
          </div>
        </dl>
      </div>
      <button type="button" className="res-more" aria-expanded={open} onClick={onToggle}>
        {T.results.characteristics}
        <ChevronDown size={18} aria-hidden="true" className={open ? 'flip' : ''} />
      </button>
      {open && (
        <div className="res-more-body">
          {characteristics.length > 0 && (
            <div className="res-chars">
              {characteristics.map((c, i) => (
                <span key={i} className="chip">
                  {[c.name, [c.value, c.unit].filter(Boolean).join(' ')].filter(Boolean).join(': ')}
                </span>
              ))}
            </div>
          )}
          <div className="res-meta res-full">
            <span className="res-meta-label">{T.results.sourceText}:</span> {item.raw_text}
          </div>
        </div>
      )}
    </article>
  )
}

export function Results({ job, onChanged }: { job: Job; onChanged: () => void }) {
  const [filters, setFilters] = useState<string[]>([]) // several statuses can be shown at once
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [data, setData] = useState<{ total: number; items: ItemRow[] } | null>(null)
  const [version, setVersion] = useState(0)
  const [showAll, setShowAll] = useState(false)
  const [opened, setOpened] = useState<Set<number>>(new Set())
  const [threshold, setThreshold] = useState(85)
  const [toast, toastHolder] = message.useMessage()

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [search])

  // A row that failed to process is shown together with the ones that need a look
  const status = filters.flatMap((f) => (f === 'needs_review' ? [f, 'failed'] : [f])).join(',')

  useEffect(() => {
    let alive = true
    api
      .items(job.id, { status, q: query, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE })
      .then((next) => alive && setData(next))
      .catch((e) => alive && toast.error(e instanceof ApiError ? e.message : T.errors.network))
    return () => {
      alive = false
    }
    // job.processed: new rows of a running job appear without touching what is open
  }, [job.id, job.processed, status, query, page, version, toast])

  const acceptAll = async () => {
    try {
      const { accepted } = await api.acceptAll(job.id, threshold)
      toast.success(T.results.accepted(accepted))
      setVersion((v) => v + 1)
      onChanged()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : T.errors.network)
    }
  }

  const counts = job.counts
  // Review progress: rows with a settled code against rows that still need a look
  const done = (counts?.auto ?? 0) + (counts?.corrected ?? 0) + (counts?.accepted ?? 0)
  const left = (counts?.needs_review ?? 0) + (counts?.failed ?? 0)
  const started = (counts?.corrected ?? 0) + (counts?.accepted ?? 0) > 0
  const donePercent = job.total ? (100 * done) / job.total : 0

  /** Open the first row that still needs a look; "Enter" there goes on to the next ones. */
  const startReview = async () => {
    try {
      const first = await api.items(job.id, { status: 'needs_review,failed', limit: 1, offset: 0 })
      if (first.items[0]) navigate(paths.item(job.id, first.items[0].id))
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : T.errors.network)
    }
  }

  const filtered = Boolean(filters.length || search)
  const reset = () => {
    setFilters([])
    setSearch('')
    setQuery('')
    setPage(1)
  }

  const toggle = (id: number) =>
    setOpened((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  return (
    <>
      {toastHolder}
      <div className="results-grid">
        <div className="results-main">
          <section className="review" aria-label={T.results.review.start}>
            <div className="review-text">
              <div className="review-title num">{T.results.review.done(done, job.total)}</div>
              <div className="muted num">
                {left > 0 ? T.results.review.left(left) : T.results.review.allDone}
              </div>
            </div>
            <div className="bar review-bar" aria-hidden="true">
              <span className="accent-fill" style={{ width: `${donePercent}%` }} />
            </div>
            {left > 0 && (
              <button
                type="button"
                className="btn primary"
                onClick={() => void startReview()}
                title={T.results.review.hint}
              >
                <Eye size={16} aria-hidden="true" />{' '}
                {started ? T.results.review.resume : T.results.review.start}
              </button>
            )}
          </section>

          {Boolean(job.fallback) && (
            <div className="alert warn" role="status">
              <Info size={18} aria-hidden="true" style={{ flex: 'none' }} />
              {T.results.fallback(job.fallback ?? 0, job.total)}
            </div>
          )}

          <div className="list-bar">
            <Checkbox checked={showAll} onChange={(e) => setShowAll(e.target.checked)}>
              {T.results.showAttrs}
            </Checkbox>
            {data && (
              <span className="muted num">
                {T.results.pageTotal(
                  data.total ? (page - 1) * PAGE_SIZE + 1 : 0,
                  Math.min(page * PAGE_SIZE, data.total),
                  data.total,
                )}
              </span>
            )}
          </div>

          {data === null ? (
            [0, 1, 2].map((i) => <Skeleton key={i} height={170} style={{ marginBottom: 16 }} />)
          ) : data.items.length === 0 ? (
            <EmptyState title={T.results.empty} hint={T.results.emptyHint} />
          ) : (
            <div className="res-list">
              {data.items.map((item) => (
                <ResultCard
                  key={item.id}
                  item={item}
                  jobId={job.id}
                  open={showAll || opened.has(item.id)}
                  onToggle={() => toggle(item.id)}
                />
              ))}
            </div>
          )}

          {data !== null && data.total > PAGE_SIZE && (
            <Pagination
              className="pager"
              current={page}
              pageSize={PAGE_SIZE}
              total={data.total}
              showSizeChanger={false}
              onChange={(next) => {
                setPage(next)
                window.scrollTo(0, 0)
              }}
            />
          )}
        </div>

        <aside className="params" aria-label={T.results.params}>
          <h2 className="params-title">{T.results.params}</h2>

          <div className="param">
            <label className="param-label" htmlFor="list-search">
              {T.results.search}
            </label>
            <Input
              id="list-search"
              allowClear
              value={search}
              onChange={(e) => {
                setSearch(e.target.value)
                setPage(1)
              }}
              prefix={<Search size={15} aria-hidden="true" />}
              placeholder={T.results.searchHint}
            />
          </div>

          <div className="param" role="group" aria-label={T.results.statusFilter}>
            <div className="param-label">{T.results.statusFilter}</div>
            {FILTERS.map((key) => {
              const value = (counts?.[key] ?? 0) + (key === 'needs_review' ? (counts?.failed ?? 0) : 0)
              return (
                <button
                  key={key}
                  type="button"
                  className="filter-row"
                  role="checkbox"
                  aria-checked={filters.includes(key)}
                  onClick={() => {
                    setFilters(filters.includes(key) ? filters.filter((f) => f !== key) : [...filters, key])
                    setPage(1)
                  }}
                >
                  <span className="filter-box" aria-hidden="true" />
                  <span className="filter-name">{T.results.statuses[key]}</span>
                  <span className="filter-count num">{value}</span>
                </button>
              )
            })}
          </div>

          <div className="param">
            <label className="param-label" htmlFor="threshold">
              {T.results.acceptTitle}
            </label>
            <div className="accept-row">
              <span className="muted">{T.results.acceptFrom}</span>
              <InputNumber
                id="threshold"
                min={0}
                max={100}
                value={threshold}
                onChange={(v) => setThreshold(v ?? 85)}
                suffix="%"
                style={{ width: 92 }}
              />
            </div>
            <button type="button" className="btn primary block" onClick={() => void acceptAll()}>
              <CheckCheck size={16} aria-hidden="true" /> {T.results.acceptButton}
            </button>
          </div>

          {filtered && (
            <button type="button" className="btn block" onClick={reset}>
              {T.results.reset}
            </button>
          )}
        </aside>
      </div>
    </>
  )
}
