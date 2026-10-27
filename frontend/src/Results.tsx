import { Checkbox, Input, InputNumber, message, Select, Switch } from 'antd'
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  CheckCheck,
  ChevronDown,
  Eye,
  Search,
  SlidersHorizontal,
  Undo2,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, ApiError, type ItemRow, type Job, type Sort } from './api'
import { useHealth } from './health'
import { Link } from './Link'
import {
  forgetItem,
  lastItemOf,
  navigate,
  paths,
  saveListView,
  savedListView,
  type ListView,
} from './router'
import { T } from './texts'
import { Confidence, EmptyState, Notice, Pager, Skeleton, StatusChip } from './ui'

const PAGE_SIZE = 20
const FILTERS = ['auto', 'needs_review', 'corrected', 'accepted'] as const
const COLUMNS: { sort: Sort; label: string; className: string }[] = [
  { sort: 'code', label: T.results.columns.name, className: 'res-head-name' },
  { sort: 'confidence', label: T.results.columns.confidence, className: 'res-head-conf' },
  { sort: 'status', label: T.results.columns.status, className: 'res-head-status' },
]

/** The caption of a column that sorts the list: click goes ascending, descending, off. */
function SortCaption({ column, sort, order, onSort }: {
  column: (typeof COLUMNS)[number]
  sort: Sort | ''
  order: 'asc' | 'desc'
  onSort: (sort: Sort) => void
}) {
  const on = sort === column.sort
  const Icon = !on ? ArrowUpDown : order === 'asc' ? ArrowUp : ArrowDown
  return (
    <button
      type="button"
      className={`res-sort ${column.className} ${on ? 'on' : ''}`}
      aria-label={T.results.sort.by(column.label, on ? T.results.sort[order] : '')}
      onClick={() => onSort(column.sort)}
    >
      {column.label}
      <Icon size={14} aria-hidden="true" />
    </button>
  )
}

/** One position: its row number, the code with its name, the confidence and the status.
 *  When positions are picked, a returnable one shows a check box in place of the number. */
function ResultCard({ item, jobId, open, onToggle, pick }: {
  item: ItemRow
  jobId: string
  open: boolean
  onToggle: () => void
  pick?: { checked: boolean; onChange: () => void }
}) {
  const to = paths.item(jobId, item.id)
  const characteristics = item.attributes.characteristics ?? []
  return (
    <article className="res-card">
      <div className="res-top">
        {pick ? (
          <span className="row-badge pick">
            <Checkbox
              checked={pick.checked}
              onChange={pick.onChange}
              aria-label={T.results.pick.row(item.row_number)}
            />
          </span>
        ) : (
          <span className="row-badge num" title={T.results.rowNumber(item.row_number)}>
            <span className="sr-only">{T.results.row} </span>
            {item.row_number}
          </span>
        )}
        <div className="res-title">
          <Link className={`res-code ${item.ktru_code ? '' : 'none'}`} to={to} data-item={item.id}>
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
  // What the person set up in this list is kept: back from a position it opens the same page
  const saved = savedListView(job.id)
  const [filters, setFilters] = useState<string[]>(saved?.filters ?? []) // several at once
  const [search, setSearch] = useState(saved?.search ?? '')
  const [query, setQuery] = useState((saved?.search ?? '').trim())
  const [page, setPage] = useState(saved?.page ?? 1)
  const [sort, setSort] = useState<Sort | ''>(saved?.sort ?? '')
  const [order, setOrder] = useState<ListView['order']>(saved?.order ?? 'asc')
  const [data, setData] = useState<{ total: number; items: ItemRow[] } | null>(null)
  const [version, setVersion] = useState(0)
  const [showAll, setShowAll] = useState(false)
  const [flipped, setFlipped] = useState<Set<number>>(new Set()) // cards that differ from "show all"
  const [picking, setPicking] = useState(false)
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [threshold, setThreshold] = useState<number | null>(85) // null: the field is cleared
  const [toast, toastHolder] = message.useMessage()
  const health = useHealth()
  const noKeys = typeof health === 'object' && health !== null && health.llm === 'off'

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [search])

  // A row that failed to process is shown together with the ones that need a look
  const status = filters.flatMap((f) => (f === 'needs_review' ? [f, 'failed'] : [f])).join(',')

  useEffect(() => saveListView(job.id, { page, filters, search, sort, order }), [
    job.id,
    page,
    filters,
    search,
    sort,
    order,
  ])

  useEffect(() => {
    let alive = true
    api
      .items(job.id, {
        status,
        q: query,
        sort,
        order,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      })
      .then((next) => {
        if (!alive) return
        // The page is past the end (rows were returned to review, a filter shrank the list)
        if (next.items.length === 0 && next.total > 0 && page > 1) {
          setPage(Math.ceil(next.total / PAGE_SIZE))
        } else {
          setData(next)
        }
      })
      .catch((e) => alive && toast.error(e instanceof ApiError ? e.message : T.errors.network))
    return () => {
      alive = false
    }
    // job.processed: new rows of a running job appear without touching what is open
  }, [job.id, job.processed, status, query, sort, order, page, version, toast])

  // Back from a position: the focus goes to its row, so the keyboard does not start from the top
  const [returnTo] = useState(() => lastItemOf(job.id))
  useEffect(() => {
    if (data === null || returnTo === null) return
    const row = document.querySelector<HTMLElement>(`[data-item="${returnTo}"]`)
    row?.focus()
    row?.scrollIntoView({ block: 'center' })
    forgetItem()
  }, [data, returnTo])

  const acceptAll = async () => {
    if (threshold === null) return
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

  // The switch opens or closes every card; a card can still be flipped on its own after that
  const toggle = (id: number) =>
    setFlipped((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  const showAllCards = (on: boolean) => {
    setShowAll(on)
    setFlipped(new Set())
  }

  const firstOrder = (column: Sort): ListView['order'] => (column === 'confidence' ? 'desc' : 'asc')
  /** A click on a caption: its first order, the opposite one, then no sorting. */
  const sortBy = (column: Sort) => {
    if (sort !== column) {
      setSort(column)
      setOrder(firstOrder(column))
    } else if (order === firstOrder(column)) {
      setOrder(order === 'asc' ? 'desc' : 'asc')
    } else {
      setSort('')
      setOrder('asc')
    }
    setPage(1)
  }
  const sortValue = sort ? `${sort}:${order}` : ''
  const sortOptions = [
    { value: '', label: T.results.sort.none },
    ...Object.entries(T.results.sort.options).map(([value, label]) => ({ value, label })),
  ]

  const returnable = (item: ItemRow) => item.status === 'accepted' || item.status === 'corrected'
  const pageRows = (data?.items ?? []).filter(returnable)
  const pageAllPicked = pageRows.length > 0 && pageRows.every((item) => picked.has(item.id))
  const pickable = (counts?.accepted ?? 0) + (counts?.corrected ?? 0) > 0
  const pick = (id: number) =>
    setPicked((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  const stopPicking = () => {
    setPicking(false)
    setPicked(new Set())
  }
  const revertMany = async (which: { ids: number[] } | { all_accepted: true }) => {
    try {
      const { reverted } = await api.revertMany(job.id, which)
      toast.success(T.results.pick.reverted(reverted))
      stopPicking()
      setVersion((v) => v + 1)
      onChanged()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : T.errors.network)
    }
  }

  return (
    <>
      {toastHolder}
      {Boolean(job.fallback) && (
        <Notice kind="info" title={noKeys ? T.results.fallbackNoKeysTitle : T.results.fallbackTitle}>
          {(noKeys ? T.results.fallbackNoKeys : T.results.fallback)(job.fallback ?? 0, job.total)}
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
                onChange={setThreshold}
                status={threshold === null ? 'error' : undefined}
                aria-describedby={threshold === null ? 'threshold-error' : undefined}
                suffix="%"
                style={{ width: 96 }}
              />
              <button
                type="button"
                className="btn primary"
                disabled={threshold === null}
                onClick={() => void acceptAll()}
              >
                <CheckCheck size={16} aria-hidden="true" /> {T.results.acceptButton}
              </button>
              {threshold === null && (
                <p className="field-error" id="threshold-error" role="alert">
                  {T.results.thresholdEmpty}
                </p>
              )}
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
        <div className="list-tools">
          <Select
            className="sort-select"
            value={sortValue}
            options={sortOptions}
            onChange={(value) => {
              const [by, how] = value.split(':')
              setSort(by as Sort | '')
              setOrder(how === 'desc' ? 'desc' : 'asc')
              setPage(1)
            }}
            aria-label={T.results.sort.label}
          />
          {pickable && (
            <button
              type="button"
              className="btn small"
              aria-pressed={picking}
              title={T.results.pick.hint}
              onClick={() => (picking ? stopPicking() : setPicking(true))}
            >
              {picking ? T.results.pick.done : T.results.pick.start}
            </button>
          )}
          <label className="switch-row">
            <Switch checked={showAll} onChange={showAllCards} aria-label={T.results.showAttrs} />
            {T.results.showAttrs}
          </label>
        </div>
      </div>

      {picking && (
        <div className="pick-bar" role="group" aria-label={T.results.pick.start}>
          <Checkbox
            checked={pageAllPicked}
            indeterminate={!pageAllPicked && pageRows.some((item) => picked.has(item.id))}
            disabled={pageRows.length === 0}
            onChange={(e) =>
              setPicked((prev) => {
                const next = new Set(prev)
                for (const item of pageRows) {
                  if (e.target.checked) next.add(item.id)
                  else next.delete(item.id)
                }
                return next
              })
            }
          >
            {T.results.pick.page}
          </Checkbox>
          <span className="muted num" aria-live="polite">
            {T.results.pick.count(picked.size)}
          </span>
          <span className="grow" />
          {(counts?.accepted ?? 0) > 0 && (
            <button
              type="button"
              className="btn small"
              onClick={() => void revertMany({ all_accepted: true })}
            >
              {T.results.pick.revertAll(counts?.accepted ?? 0)}
            </button>
          )}
          <button
            type="button"
            className="btn primary small"
            disabled={picked.size === 0}
            onClick={() => void revertMany({ ids: [...picked] })}
          >
            <Undo2 size={16} aria-hidden="true" /> {T.results.pick.revert}
          </button>
        </div>
      )}

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
        <div className="res-tray">
          <div className="res-head" role="group" aria-label={T.results.sort.label}>
            {COLUMNS.map((column) => (
              <SortCaption key={column.sort} column={column} sort={sort} order={order} onSort={sortBy} />
            ))}
          </div>
          <div className="res-list">
            {data.items.map((item) => (
              <ResultCard
                key={item.id}
                item={item}
                jobId={job.id}
                open={showAll !== flipped.has(item.id)}
                onToggle={() => toggle(item.id)}
                pick={
                  picking && returnable(item)
                    ? { checked: picked.has(item.id), onChange: () => pick(item.id) }
                    : undefined
                }
              />
            ))}
          </div>
        </div>
      )}

      {data !== null && data.total > PAGE_SIZE && (
        <Pager
          current={page}
          total={data.total}
          pageSize={PAGE_SIZE}
          onChange={(next) => {
            setPage(next)
            window.scrollTo(0, 0)
          }}
        />
      )}
    </>
  )
}
