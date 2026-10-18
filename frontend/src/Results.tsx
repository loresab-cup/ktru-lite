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
import { useEffect, useState, type KeyboardEvent } from 'react'
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
    width: 64,
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
    width: '20%',
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
    width: '26%',
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
    width: 120,
    render: (_, item) => <Confidence value={item.confidence} zone={item.zone} />,
  },
  {
    title: T.results.columns.status,
    width: 160,
    render: (_, item) => <StatusChip status={item.status} />,
  },
]

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
      .then((next) => alive && setData(next))
      .catch((e) => alive && toast.error(e instanceof ApiError ? e.message : T.errors.network))
    return () => {
      alive = false
    }
  }, [job.id, job.processed, job.status, filter, query, page, version, toast])

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
  const seconds = job.stage_seconds.total

  return (
    <>
      {toastHolder}
      <div className="results-head">
        <div className="meta num">
          {T.results.positions(job.total)}
          {seconds !== undefined && ` · ${T.results.took(seconds)}`}
        </div>
      </div>
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
            scroll={{ x: 1000 }}
            size="middle"
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
        onClose={() => setSelected(null)}
        onChanged={(item) => {
          setSelected(item)
          reload()
        }}
      />
      <ExportButton jobId={job.id} />
    </>
  )
}
