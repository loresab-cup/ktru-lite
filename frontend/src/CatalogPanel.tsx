import { App as AntApp, Checkbox, Input, Switch } from 'antd'
import { ChevronDown, ExternalLink, Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, ApiError, ktruUrl, type CatalogCode } from './api'
import { capital, T } from './texts'
import { Skeleton } from './ui'

const MIN_LETTERS = 2 // the service finds nothing useful by one letter
const STEP = 20 // rows shown at first and added by "Show more"
const LIMIT = 500 // codes asked for at once: past that the query is worth narrowing

/** One found code: the code, its state, the name, its characteristics folded as in the list of
 *  a specification, and a link to the same code in the EIS. */
function CodeRow({ code, open, onToggle }: { code: CatalogCode; open: boolean; onToggle: () => void }) {
  const active = code.status === 'ACTIVE'
  const url = ktruUrl(code.code)
  const count = code.characteristics.length
  return (
    <li className="found-row">
      <div className="found-main">
        <div className="found-top">
          <span className="code num">{code.code}</span>
          <span className={`status ${active ? 'status-accepted' : 'status-corrected'}`}>
            <span className="status-dot" aria-hidden="true" />
            {capital(T.codeStatus[code.status] ?? code.status)}
          </span>
        </div>
        <div className="found-name">
          <span className="sentence">{code.name}</span>
          {code.okpd2_code && (
            <span className="muted num">
              {T.catalogSearch.okpd2} {code.okpd2_code}
            </span>
          )}
        </div>
        {count > 0 && (
          <button type="button" className="found-more" aria-expanded={open} onClick={onToggle}>
            {T.catalogSearch.chars(count)}
            <ChevronDown size={16} aria-hidden="true" className={open ? 'flip' : ''} />
          </button>
        )}
        {count > 0 && open && (
          <div className="chips">
            {code.characteristics.map(([name, values]) => (
              <span key={name} className="chip">
                {name}: {values.join('; ')}
              </span>
            ))}
          </div>
        )}
      </div>
      {url && (
        <a className="btn small" href={url} target="_blank" rel="noopener noreferrer" title={T.results.openEis}>
          {T.item.openEis} <ExternalLink size={15} aria-hidden="true" />
        </a>
      )}
    </li>
  )
}

/** Search of the whole catalog by code or by words of the name, without any file. */
export function CatalogPanel() {
  const { message } = AntApp.useApp()
  const [query, setQuery] = useState('')
  const [inactive, setInactive] = useState(false)
  const [found, setFound] = useState<CatalogCode[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [shown, setShown] = useState(STEP)
  // The switch opens or closes every row; a row can still be flipped on its own after that
  const [showAll, setShowAll] = useState(false)
  const [flipped, setFlipped] = useState<Set<string>>(new Set())
  const toggle = (code: string) =>
    setFlipped((prev) => {
      const next = new Set(prev)
      if (next.has(code)) next.delete(code)
      else next.add(code)
      return next
    })

  const q = query.trim()
  useEffect(() => {
    if (q.length < MIN_LETTERS) return
    let alive = true
    const timer = window.setTimeout(() => {
      setLoading(true)
      api
        .catalog(q, LIMIT)
        .then((codes) => {
          if (!alive) return
          setFound(codes)
          setShown(STEP)
          setFlipped(new Set())
        })
        .catch((e) => {
          if (!alive) return
          setFound([])
          message.error(e instanceof ApiError ? e.message : T.catalogSearch.failed)
        })
        .finally(() => alive && setLoading(false))
    }, 300)
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [q, message])

  const ready = q.length >= MIN_LETTERS ? found : null
  const list = (ready ?? []).filter((code) => inactive || code.status === 'ACTIVE')
  const hiddenInactive = (ready?.length ?? 0) > 0 && list.length === 0
  // Active codes come first, so a cut answer misses only inactive ones unless all are shown
  const cut = (ready?.length ?? 0) >= LIMIT && list.length === ready?.length

  return (
    <div className="catalog-panel">
      <Input
        size="large"
        className="big-input"
        allowClear
        autoFocus
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        prefix={<Search size={20} aria-hidden="true" />}
        placeholder={T.catalogSearch.placeholder}
        aria-label={T.catalogSearch.label}
      />
      <div className="drop-options">
        <Checkbox checked={inactive} onChange={(e) => setInactive(e.target.checked)}>
          {T.catalogSearch.inactive}
        </Checkbox>
      </div>
      <div aria-live="polite">
        {q.length < MIN_LETTERS && <p className="muted">{T.catalogSearch.prompt}</p>}
        {(loading || (q.length >= MIN_LETTERS && ready === null)) && <Skeleton height={96} />}
        {!loading && ready !== null && list.length > 0 && (
          <>
            <div className="found-head">
              <div className="found-count">
                <h2 className="section-h">{T.catalogSearch.found(list.length)}</h2>
                <span className="muted">{T.catalogSearch.activeFirst}</span>
              </div>
              <label className="switch-row">
                <Switch
                  checked={showAll}
                  onChange={(on) => {
                    setShowAll(on)
                    setFlipped(new Set())
                  }}
                  aria-label={T.catalogSearch.showChars}
                />
                {T.catalogSearch.showChars}
              </label>
            </div>
            {cut && <p className="muted">{T.catalogSearch.limited(LIMIT)}</p>}
            <ul className="found-list">
              {list.slice(0, shown).map((code) => (
                <CodeRow
                  key={code.code}
                  code={code}
                  open={showAll !== flipped.has(code.code)}
                  onToggle={() => toggle(code.code)}
                />
              ))}
            </ul>
            {list.length > shown && (
              <div className="more-row">
                <button type="button" className="btn" onClick={() => setShown(shown + STEP)}>
                  {T.catalogSearch.showMore}
                </button>
              </div>
            )}
          </>
        )}
        {!loading && ready !== null && list.length === 0 && (
          <div className="found-empty">
            <b>{hiddenInactive ? T.catalogSearch.onlyInactive : T.catalogSearch.empty}</b>
            {!hiddenInactive && <span className="muted">{T.catalogSearch.emptyHint}</span>}
          </div>
        )}
      </div>
    </div>
  )
}
