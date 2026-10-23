import { Dropdown, Input, InputNumber, message, Table, type TableColumnsType } from 'antd'
import {
  CheckCheck,
  Download,
  Eye,
  Info,
  FileSpreadsheet,
  FileText,
  PenLine,
  Search,
  Sparkles,
} from 'lucide-react'
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { api, ApiError, type Item, type Job } from './api'
import { ItemDrawer } from './ItemDrawer'
import { T } from './texts'
import { Confidence, EmptyState, KtruCode, Skeleton, StatusChip } from './ui'

const PAGE_SIZE = 50
const TILES = [
  { key: 'auto', icon: Sparkles },
  { key: 'needs_review', icon: Eye },
  { key: 'corrected', icon: PenLine },
  { key: 'accepted', icon: CheckCheck },
] as const

const columns: TableColumnsType<Item> = [
  {
    title: T.results.columns.row,
    dataIndex: 'row_number',
    width: 56,
    responsive: ['md'],
    render: (value: number) => <span className="num">{value}</span>,
  },
  {
    title: T.results.columns.raw,
    dataIndex: 'raw_text',
    responsive: ['lg'], // narrow screens: the raw text is in the item panel
    render: (value: string) => (
      <span className="cell-raw" title={value}>
        {value}
      </span>
    ),
  },
  {
    title: T.results.columns.attrs,
    width: 180,
    responsive: ['md'],
    render: (_, item) => (
      <>
        <div style={{ fontWeight: 600 }}>{item.attributes.name || T.drawer.none}</div>
        <div className="cell-sub">
          {[item.attributes.brand, item.attributes.model].filter(Boolean).join(' · ') ||
            T.drawer.none}
        </div>
      </>
    ),
  },
  {
    title: T.results.columns.code,
    width: 220,
    render: (_, item) =>
      item.ktru_code ? (
        <>
          <KtruCode code={item.ktru_code} />
          <div className="cell-sub cell-raw">{item.ktru_name}</div>
        </>
      ) : (
        <span className="cell-sub">{T.results.noCode}</span>
      ),
  },
  {
    title: T.results.columns.confidence,
    width: 128,
    render: (_, item) => <Confidence value={item.confidence} zone={item.zone} />,
  },
  {
    title: T.results.columns.status,
    width: 156,
    responsive: ['md'], // phones: the zone icon of the confidence says the same
    render: (_, item) => <StatusChip status={item.status} />,
  },
]

/** A row that nobody has looked at yet: neither accepted nor corrected by hand. */
const unreviewed = (item: Item) => item.status !== 'accepted' && item.status !== 'corrected'

type Pending = 'first' | 'last' | 'unreviewed'

/** The row to open on a freshly loaded page when the panel stepped over the page edge. */
function pickRow(items: Item[], pending: Pending): Item | null {
  if (pending === 'last') return items[items.length - 1] ?? null
  if (pending === 'unreviewed') return items.find(unreviewed) ?? items[0] ?? null
  return items[0] ?? null
}

/** Arrow keys move between rows, Enter opens the row (keys of a link inside are its own). */
function rowKeys(event: KeyboardEvent<HTMLElement>, open: () => void) {
  if (event.target !== event.currentTarget) return
  const row = event.currentTarget
  const target =
    event.key === 'ArrowDown'
      ? row.nextElementSibling
      : event.key === 'ArrowUp'
        ? row.previousElementSibling
        : null
  if (event.key === 'Enter') open()
  if (target instanceof HTMLElement && target.tabIndex === 0) {
    event.preventDefault()
    target.focus()
  }
}

function ExportButton({ jobId }: { jobId: string }) {
  const items = [
    { key: 'xlsx', icon: <FileSpreadsheet size={16} />, label: T.results.exportXlsx },
    { key: 'csv', icon: <FileText size={16} />, label: T.results.exportCsv },
  ] as const
  return (
    <Dropdown
      trigger={['click']}
      placement="topRight"
      autoFocus // keyboard: the menu takes focus, arrows and Enter pick the format
      menu={{
        items: items.map(({ key, icon, label }) => ({ key, icon, label })),
        // A menu item, not a link inside it: works with the mouse and with Enter alike;
        // the file is an attachment, so the page stays where it is
        onClick: ({ key }) => window.location.assign(api.exportUrl(jobId, key as 'xlsx' | 'csv')),
      }}
    >
      <button type="button" className="round fab" aria-label={T.results.export} title={T.results.export}>
        <Download size={24} aria-hidden="true" />
      </button>
    </Dropdown>
  )
}

export function Results({ job, onChanged }: { job: Job; onChanged: () => void }) {
  const [filter, setFilter] = useState('')
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [data, setData] = useState<{ total: number; items: Item[] } | null>(null)
  // Which row to open when the panel walks onto another page; read when that page arrives
  const pending = useRef<Pending | null>(null)
  const [version, setVersion] = useState(0)
  const [selected, setSelected] = useState<Item | null>(null)
  const [threshold, setThreshold] = useState(85)
  const [toast, toastHolder] = message.useMessage()

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [search])

  useEffect(() => {
    let alive = true
    const status = filter === 'needs_review' ? 'needs_review,failed' : filter
    api
      .items(job.id, { status, q: query, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE })
      .then((next) => {
        if (!alive) return
        setData(next)
        if (pending.current) {
          setSelected(pickRow(next.items, pending.current))
          pending.current = null
        }
      })
      .catch((e) => alive && toast.error(e instanceof ApiError ? e.message : T.errors.network))
    return () => {
      alive = false
    }
    // job.processed: new rows of a running job appear without touching what is open for editing
  }, [job.id, job.processed, filter, query, page, version, toast])

  const items = data?.items ?? []
  const index = selected ? items.findIndex((item) => item.id === selected.id) : -1
  const pages = data ? Math.ceil(data.total / PAGE_SIZE) : 1
  const position =
    data && index >= 0 ? { index: (page - 1) * PAGE_SIZE + index + 1, total: data.total } : undefined

  /** Previous or next row of the table, across pages too. */
  const move = (step: 1 | -1) => {
    const next = items[index + step]
    if (next) return setSelected(next)
    if (step === 1 && page < pages) {
      pending.current = 'first'
      setPage(page + 1)
    }
    if (step === -1 && page > 1) {
      pending.current = 'last'
      setPage(page - 1)
    }
  }

  /** After "accept": the next row below that still needs a look. */
  const acceptNext = () => {
    const next = items.slice(index + 1).find(unreviewed)
    if (next) return setSelected(next)
    if (page < pages) {
      pending.current = 'unreviewed'
      setPage(page + 1)
      return
    }
    setSelected(null)
    toast.success(T.results.reviewDone)
  }

  const reload = () => {
    setVersion((v) => v + 1)
    onChanged()
  }

  const acceptAll = async () => {
    try {
      const { accepted } = await api.acceptAll(job.id, threshold)
      toast.success(T.results.accepted(accepted))
      reload()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : T.errors.network)
    }
  }

  const counts = job.counts
  const finished = job.status === 'done' || job.status === 'cancelled'
  const seconds = finished ? job.stage_seconds.total : undefined
  // Review progress: rows with a settled code against rows that still need a look
  const done = (counts?.auto ?? 0) + (counts?.corrected ?? 0) + (counts?.accepted ?? 0)
  const left = (counts?.needs_review ?? 0) + (counts?.failed ?? 0)
  const started = (counts?.corrected ?? 0) + (counts?.accepted ?? 0) > 0
  const donePercent = job.total ? (100 * done) / job.total : 0

  /** Show only the rows that need a look and open the first of them. */
  const startReview = () => {
    pending.current = 'unreviewed'
    setFilter('needs_review')
    setSearch('')
    setQuery('') // at once, not after the search pause
    setPage(1)
    setVersion((v) => v + 1) // load even when this filter is already on
  }

  return (
    <>
      {toastHolder}
      <div className="results-head">
        <div className="meta num">
          {T.results.positions(job.total)}
          {seconds !== undefined && ` · ${T.results.took(seconds)}`}
        </div>
      </div>
      <section className="review card" aria-label={T.results.review.start}>
        <div className="review-text">
          <div className="review-title num">{T.results.review.done(done, job.total)}</div>
          <div className="cell-sub num">
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
            onClick={startReview}
            title={T.results.review.hint}
          >
            <Eye size={16} aria-hidden="true" />{' '}
            {started ? T.results.review.resume : T.results.review.start}
          </button>
        )}
      </section>

      {Boolean(job.fallback) && (
        <div className="alert warn" role="status" style={{ margin: '0 0 20px' }}>
          <Info size={18} aria-hidden="true" style={{ flex: 'none' }} />
          {T.results.fallback(job.fallback ?? 0, job.total)}
        </div>
      )}

      <div className="tiles" role="group" aria-label={T.results.tileHint}>
        {TILES.map(({ key, icon: Icon }) => {
          const value =
            (counts?.[key] ?? 0) + (key === 'needs_review' ? (counts?.failed ?? 0) : 0)
          return (
            <button
              key={key}
              type="button"
              className="tile card"
              aria-pressed={filter === key}
              title={T.results.tileHint}
              onClick={() => {
                setFilter(filter === key ? '' : key)
                setPage(1)
              }}
            >
              <span className="tile-icon" aria-hidden="true">
                <Icon size={20} />
              </span>
              <span>
                <div className="tile-value num">{value}</div>
                <div className="tile-label">{T.results.tiles[key]}</div>
              </span>
            </button>
          )
        })}
      </div>

      <section className="table-panel card">
        <div className="toolbar">
          <Input
            allowClear
            value={search}
            onChange={(e) => {
              setSearch(e.target.value)
              setPage(1)
            }}
            prefix={<Search size={15} aria-hidden="true" />}
            placeholder={T.results.search}
            aria-label={T.results.search}
            style={{ maxWidth: 340 }}
          />
          {filter && (
            <button type="button" className="btn" onClick={() => setFilter('')}>
              {T.results.reset}
            </button>
          )}
          <span className="grow" />
          <label className="cell-sub" htmlFor="threshold">
            {T.results.acceptFrom}
          </label>
          <InputNumber
            id="threshold"
            min={0}
            max={100}
            value={threshold}
            onChange={(v) => setThreshold(v ?? 85)}
            suffix="%"
            style={{ width: 96 }}
          />
          <button type="button" className="btn primary" onClick={acceptAll}>
            <CheckCheck size={16} aria-hidden="true" /> {T.results.acceptButton}
          </button>
        </div>

        {data === null ? (
          [0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} height={44} style={{ margin: 10 }} />)
        ) : (
          <Table<Item>
            rowKey="id"
            size="small"
            sticky
            scroll={{ x: 350 }}
            columns={columns}
            dataSource={data.items}
            locale={{
              emptyText: <EmptyState title={T.results.empty} hint={T.results.emptyHint} />,
            }}
            onRow={(item) => ({
              tabIndex: 0,
              className: 'row-click',
              onClick: () => setSelected(item),
              onKeyDown: (e) => rowKeys(e, () => setSelected(item)),
            })}
            pagination={{
              current: page,
              pageSize: PAGE_SIZE,
              total: data.total,
              showSizeChanger: false,
              hideOnSinglePage: true,
              onChange: setPage,
              showTotal: (total, [from, to]) => T.results.pageTotal(from, to, total),
            }}
          />
        )}
      </section>

      <ItemDrawer
        item={selected}
        position={position}
        onMove={move}
        onAcceptNext={acceptNext}
        onClose={() => setSelected(null)}
        onChanged={(item) => {
          setSelected(item)
          reload()
        }}
      />
      {finished && <ExportButton jobId={job.id} />}
    </>
  )
}
