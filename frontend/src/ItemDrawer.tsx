import { Drawer, Input, message } from 'antd'
import { Check, CheckCheck, Info, Search, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, ApiError, type CatalogCode, type Item } from './api'
import { T } from './texts'
import { Confidence, Skeleton, StatusChip } from './ui'

function CodeCard({ code, current, score, matched, onChoose }: {
  code: CatalogCode
  current: boolean
  score?: number
  matched?: string[]
  onChoose: () => void
}) {
  return (
    <div className={`cand ${current ? 'current' : ''}`}>
      <div className="cand-head">
        <div className="cand-name">
          <div className="code">{code.code}</div>
          {code.name}
        </div>
        {code.status !== 'ACTIVE' && (
          <span className="chip low">{T.codeStatus[code.status] ?? code.status}</span>
        )}
      </div>
      {matched && matched.length > 0 && (
        <div className="tags" style={{ marginTop: 8 }}>
          {matched.map((m) => (
            <span key={m} className="chip high">
              {m}
            </span>
          ))}
        </div>
      )}
      <div className="cand-foot">
        {score !== undefined && (
          <>
            <span className="cell-sub num">
              {T.drawer.similarity} {score.toFixed(2)}
            </span>
            <div className="bar" aria-hidden="true">
              <span className="accent-fill" style={{ width: `${score * 100}%` }} />
            </div>
          </>
        )}
        {score === undefined && <span style={{ flex: 1 }} />}
        <button
          type="button"
          className={current ? 'btn' : 'btn primary'}
          onClick={onChoose}
          disabled={current}
          style={{ height: 34 }}
        >
          {current ? <Check size={15} aria-hidden="true" /> : null}
          {current ? T.drawer.chosen : T.drawer.choose}
        </button>
      </div>
    </div>
  )
}

function CatalogSearch({ item, onChoose }: { item: Item; onChoose: (code: string) => void }) {
  const [query, setQuery] = useState('')
  const [found, setFound] = useState<CatalogCode[] | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    const q = query.trim()
    if (q.length < 2) return
    let alive = true
    const timer = window.setTimeout(() => {
      setLoading(true)
      api
        .catalog(q)
        .then((codes) => alive && setFound(codes))
        .catch(() => alive && setFound([]))
        .finally(() => alive && setLoading(false))
    }, 300)
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [query])

  const visible = query.trim().length >= 2 ? found : null
  return (
    <div className="section">
      <div className="section-title">{T.drawer.search}</div>
      <Input
        allowClear
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        prefix={<Search size={15} aria-hidden="true" />}
        placeholder={T.drawer.searchPlaceholder}
        aria-label={T.drawer.search}
      />
      <div style={{ marginTop: 10 }} aria-live="polite">
        {loading && <Skeleton height={60} />}
        {!loading && visible?.length === 0 && <div className="cell-sub">{T.drawer.searchEmpty}</div>}
        {!loading &&
          visible?.map((code) => (
            <CodeCard
              key={code.code}
              code={code}
              current={code.code === item.ktru_code}
              onChoose={() => onChoose(code.code)}
            />
          ))}
      </div>
    </div>
  )
}

export function ItemDrawer({ item, onClose, onChanged }: {
  item: Item | null
  onClose: () => void
  onChanged: (item: Item) => void
}) {
  const [toast, toastHolder] = message.useMessage()
  const [busy, setBusy] = useState(false)

  const run = async (action: () => Promise<Item>, done: string) => {
    setBusy(true)
    try {
      onChanged(await action())
      toast.success(done)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : T.errors.network)
    } finally {
      setBusy(false)
    }
  }

  const choose = (code: string) => item && run(() => api.correct(item.id, code), T.drawer.saved)
  const attrs = item?.attributes

  return (
    <Drawer
      open={item !== null}
      onClose={onClose}
      placement="right"
      size={520}
      destroyOnHidden
      title={item ? T.drawer.title(item.row_number) : ''}
      closeIcon={<X size={18} aria-label={T.drawer.close} />}
      extra={
        item && (
          <button
            type="button"
            className="btn primary"
            disabled={busy || !item.ktru_code || item.status === 'accepted'}
            onClick={() => run(() => api.accept(item.id), T.drawer.acceptedDone)}
          >
            <CheckCheck size={16} aria-hidden="true" /> {T.drawer.accept}
          </button>
        )
      }
    >
      {toastHolder}
      {item && attrs && (
        <>
          <div className="section" style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
            <Confidence value={item.confidence} zone={item.zone} />
            <StatusChip status={item.status} />
          </div>

          <div className="section">
            <div className="section-title">{T.drawer.source}</div>
            <div className="raw">{item.raw_text}</div>
          </div>

          <div className="section">
            <div className="section-title">{T.drawer.attributes}</div>
            <dl className="attrs">
              <dt>{T.drawer.name}</dt>
              <dd>{attrs.name || T.drawer.none}</dd>
              <dt>{T.drawer.brand}</dt>
              <dd>{attrs.brand || T.drawer.none}</dd>
              <dt>{T.drawer.model}</dt>
              <dd>{attrs.model || T.drawer.none}</dd>
              <dt>{T.drawer.characteristics}</dt>
              <dd className="tags">
                {attrs.characteristics?.length
                  ? attrs.characteristics.map((c, i) => (
                      <span key={i} className="chip">
                        {[c.name, [c.value, c.unit].filter(Boolean).join(' ')]
                          .filter(Boolean)
                          .join(': ')}
                      </span>
                    ))
                  : T.drawer.none}
              </dd>
            </dl>
            {attrs.source === 'rules' && (
              <div className="cell-sub" style={{ marginTop: 8 }}>
                <Info size={13} aria-hidden="true" style={{ verticalAlign: -2 }} /> {T.drawer.byRules}
              </div>
            )}
          </div>

          {item.summary && (
            <div className="section">
              <div className="section-title">{T.drawer.why}</div>
              <div className="summary">{item.summary}</div>
            </div>
          )}

          <div className="section">
            <div className="section-title">{T.drawer.candidates}</div>
            {item.candidates.map((c) => (
              <CodeCard
                key={c.code}
                code={c}
                score={c.score}
                matched={c.matched_characteristics}
                current={c.code === item.ktru_code}
                onChoose={() => choose(c.code)}
              />
            ))}
          </div>

          <CatalogSearch key={item.id} item={item} onChoose={choose} />
        </>
      )}
    </Drawer>
  )
}
