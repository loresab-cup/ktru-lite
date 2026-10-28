import { Input } from 'antd'
import {
  Check,
  Search
} from 'lucide-react'
import { useEffect, useState } from 'react'
import {
  api,
  zoneOf,
  type CatalogCode,
  type Item,
  type ItemPage
} from '../api'
import { capital, T } from '../texts'
import { KtruCode, Notice, Skeleton } from '../ui'


/** A code of the catalog or a candidate of the search: code, name, what matched, the button. */
function CodeCard({ code, current, confidence, matched, differs, onChoose }: {
  code: CatalogCode
  current: boolean
  confidence?: number
  matched?: string[]
  differs?: [string, string][]
  onChoose: () => void
}) {
  return (
    <div className={`cand ${current ? 'current' : ''}`}>
      <div className="cand-main">
        <div className="cand-title">
          <KtruCode code={code.code} />
          <span className="cand-name sentence">{code.name}</span>
          {code.status !== 'ACTIVE' && (
            <span className="status status-failed">
              <span className="status-dot" aria-hidden="true" />
              {capital(T.codeStatus[code.status] ?? code.status)}
            </span>
          )}
        </div>
        {confidence !== undefined && (
          <div className="cand-conf" title={T.confidenceHint}>
            {T.drawer.candidateConfidence}
            <span className={`num zone-${zoneOf(confidence)}`}>{Math.floor(confidence)} %</span>
          </div>
        )}
        {matched && matched.length > 0 && (
          <div className="cand-meta">
            {matched.map((m) => (
              <span key={m} className="chip good">
                <Check size={14} strokeWidth={2.5} aria-hidden="true" />
                {m}
              </span>
            ))}
          </div>
        )}
        {differs && differs.length > 0 && (
          <div className="cand-differs">
            <span className="muted">{T.drawer.differs}</span>
            {differs.map(([name, values]) => (
              <span key={name} className="chip">
                {name}: {values}
              </span>
            ))}
          </div>
        )}
      </div>
      <button
        type="button"
        className={`btn ${current ? 'soft' : ''}`}
        onClick={onChoose}
        disabled={current}
      >
        {current && <Check size={16} aria-hidden="true" />}
        {current ? T.drawer.chosen : T.drawer.choose}
      </button>
    </div>
  )
}


function CatalogSearch({ item, onChoose }: { item: Item; onChoose: (code: string) => void }) {
  const [query, setQuery] = useState('')
  const [found, setFound] = useState<CatalogCode[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [completed, setCompleted] = useState('')
  const [error, setError] = useState(false)

  useEffect(() => {
    const q = query.trim()
    if(q.length < 2) return
    let alive = true
    const timer = window.setTimeout(() => {
      setLoading(true)
      api
        .catalog(q)
        .then((codes) => {
          if(!alive) return
          setFound(codes)
          setError(false)
          setCompleted(q)
        })
        .catch(() => {
          if(!alive) return
          setFound(null)
          setError(true)
          setCompleted(q)
        })
        .finally(() => alive && setLoading(false))
    }, 300)
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [query])

  const current = query.trim().length >= 2 && completed === query.trim()
  const visible = current ? found : null
  return (
    <section className="card tab-card">
      <h2 className="tab-h">{T.drawer.search}</h2>
      <Input
        className="search-input"
        size="large"
        allowClear
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        prefix={<Search size={18} aria-hidden="true" />}
        placeholder={T.drawer.searchPlaceholder}
        aria-label={T.drawer.search}
      />
      <div aria-live="polite">
        {query.trim().length >= 2 && (loading || !current) && <Skeleton height={72} />}
        {current && error && <Notice kind="error" title={T.catalogSearch.failed} />}
        {!loading && visible?.length === 0 && <p className="muted">{T.drawer.searchEmpty}</p>}
        {!loading && visible && visible.length > 0 && (
          <div className="cand-list">
            <b>{T.drawer.searchFound(visible.length)}</b>
            {visible.map((code) => (
              <CodeCard
                key={code.code}
                code={code}
                current={code.code === item.ktru_code}
                onChoose={() => onChoose(code.code)}
              />
            ))}
          </div>
        )}
      </div>
    </section>
  )
}


export function CandidatesTab({ item, onChoose }: { item: ItemPage; onChoose: (code: string) => void }) {
  return (
    <>
      <CatalogSearch key={`search-${item.id}`} item={item} onChoose={onChoose} />
      <section className="card tab-card">
        <h2 className="tab-h">{T.drawer.candidates}</h2>
        <div className="cand-list">
          {item.candidates.map((c) => (
            <CodeCard
              key={c.code}
              code={c}
              confidence={c.confidence}
              matched={c.matched_characteristics}
              differs={c.differs}
              current={c.code === item.ktru_code}
              onChoose={() => onChoose(c.code)}
            />
          ))}
        </div>
      </section>
    </>
  )
}
