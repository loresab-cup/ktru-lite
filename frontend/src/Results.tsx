import { Input, InputNumber, message, Pagination, Switch } from 'antd'
import { CheckCheck, ChevronDown, Eye, Search, SlidersHorizontal } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, ApiError, type ItemRow, type Job } from './api'
import { Link } from './Link'
import { navigate, paths } from './router'
import { T } from './texts'
import { Confidence, EmptyState, Notice, Skeleton, StatusChip } from './ui'

const PAGE_SIZE = 20
const FILTERS = ['auto', 'needs_review', 'corrected', 'accepted'] as const

/** One position: its row number, the code with its name, the confidence and the status. */
function ResultCard({ item, jobId, open, onToggle }: {
  item: ItemRow
  jobId: string
  open: boolean
  onToggle: () => void
}) {
  const to = paths.item(jobId, item.id)
  const characteristics = item.attributes.characteristics ?? []
  return (
    <article className="res-card">
      <div className="res-top">
        <span className="row-badge num" title={T.results.rowNumber(item.row_number)}>
          <span className="sr-only">{T.results.row} </span>
          {item.row_number}
        </span>
        <div className="res-title">
          <Link className={`res-code ${item.ktru_code ? '' : 'none'}`} to={to}>
            {item.ktru_code ?? T.results.noCode}
          </Link>
          {item.ktru_name && (
            <Link className="res-name sentence" to={to} tabIndex={-1}>
              {item.ktru_name}
            </Link>
          )}
        </div>
        <div className="res-conf">
          <span className="cap-small">{T.results.confidence}</span>
          <Confidence value={item.confidence} zone={item.zone} />
        </div>
        <div className="res-status">
          <span className="cap-small">{T.results.status}</span>
          <StatusChip status={item.status} />
        </div>
      </div>
      <button type="button" className="res-more" aria-expanded={open} onClick={onToggle}>
        {T.results.more}
        <ChevronDown size={18} aria-hidden="true" className={open ? 'flip' : ''} />
      </button>
      {open && (
        <div className="res-more-body">
          {characteristics.length > 0 && (
            <div className="chips">
              {characteristics.map((c, i) => (
                <span key={i} className="chip">
                  {[c.name, [c.value, c.unit].filter(Boolean).join(' ')].filter(Boolean).join(': ')}
                </span>
              ))}
            </div>
          )}
          <div className="res-source">
            <div className="cap">{T.results.sourceText}</div>
            {item.raw_text}
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
      {Boolean(job.fallback) && (
        <Notice kind="info" title={T.results.fallbackTitle}>
          {T.results.fallback(job.fallback ?? 0, job.total)}
        </Notice>
      )}

      <section className="panel" aria-label={T.results.params}>
        <div className="review">
          <div className="review-text">
            <div className="review-title num">{T.results.review.done(done, job.total)}</div>
            <div className="muted num">
              {left > 0 ? T.results.review.left(left) : T.results.review.allDone}
            </div>
          </div>
          <div className="bar review-bar" aria-hidden="true">
            <span style={{ width: `${donePercent}%` }} />
          </div>
          {left > 0 && (
            <button
              type="button"
              className="btn primary"
              onClick={() => void startReview()}
              title={T.results.review.hint}
            >
              <Eye size={16} aria-hidden="true" /> {started ? T.results.review.resume : T.results.review.start}
            </button>
          )}
        </div>

        <div className="filters">
          <div className="filters-head">
            <h2 className="section-h">
              <SlidersHorizontal size={20} aria-hidden="true" /> {T.results.params}
            </h2>
            {filtered && (
              <button type="button" className="btn ghost small" onClick={reset}>
                {T.results.reset}
              </button>
            )}
          </div>
          <div className="filters-row">
            <Input
              id="list-search"
              className="grow"
              allowClear
              value={search}
              onChange={(e) => {
                setSearch(e.target.value)
                setPage(1)
              }}
              prefix={<Search size={18} aria-hidden="true" />}
              placeholder={T.results.searchHint}
              aria-label={T.results.search}
            />
            <div className="accept-row" role="group" aria-label={T.results.acceptTitle}>
              <label htmlFor="threshold">{T.results.acceptFrom}</label>
              <InputNumber
                id="threshold"
                min={0}
                max={100}
                value={threshold}
                onChange={(v) => setThreshold(v ?? 85)}
                suffix="%"
                style={{ width: 96 }}
              />
              <button type="button" className="btn primary" onClick={() => void acceptAll()}>
                <CheckCheck size={16} aria-hidden="true" /> {T.results.acceptButton}
              </button>
            </div>
          </div>
          <div className="filter-chips" role="group" aria-label={T.results.statusFilter}>
            <span className="filter-label">{T.results.statusFilter}</span>
            {FILTERS.map((key) => {
              const value = (counts?.[key] ?? 0) + (key === 'needs_review' ? (counts?.failed ?? 0) : 0)
              const on = filters.includes(key)
              return (
                <button
                  key={key}
                  type="button"
                  className={`filter-chip ${on ? 'on' : ''}`}
                  role="checkbox"
                  aria-checked={on}
                  onClick={() => {
                    setFilters(on ? filters.filter((f) => f !== key) : [...filters, key])
                    setPage(1)
                  }}
                >
                  <span className={`status-dot dot-${key}`} aria-hidden="true" />
                  {T.results.statuses[key]}
                  <span className="filter-count num">{value}</span>
                </button>
              )
            })}
          </div>
        </div>
      </section>

      <div className="list-bar">
        <span className="muted num">
          {data &&
            T.results.pageShown(
              data.total ? (page - 1) * PAGE_SIZE + 1 : 0,
              Math.min(page * PAGE_SIZE, data.total),
              data.total,
            )}
        </span>
        <label className="switch-row">
          <Switch checked={showAll} onChange={setShowAll} aria-label={T.results.showAttrs} />
          {T.results.showAttrs}
        </label>
      </div>

      {data === null ? (
        [0, 1, 2].map((i) => <Skeleton key={i} height={110} style={{ marginBottom: 16 }} />)
      ) : data.items.length === 0 ? (
        <EmptyState icon={Search} title={T.results.empty} hint={T.results.emptyHint}>
          {filtered && (
            <button type="button" className="btn" onClick={reset}>
              {T.results.reset}
            </button>
          )}
        </EmptyState>
      ) : (
        <>
          <div className="res-head" aria-hidden="true">
            <span className="res-head-name">{T.results.columns.name}</span>
            <span className="res-head-conf">{T.results.columns.confidence}</span>
            <span className="res-head-status">{T.results.columns.status}</span>
          </div>
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
        </>
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
    </>
  )
}
