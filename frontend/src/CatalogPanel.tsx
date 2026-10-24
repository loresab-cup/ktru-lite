import { App as AntApp, Checkbox, Input } from 'antd'
import { ExternalLink, Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, ApiError, ktruUrl, type CatalogCode } from './api'
import { capital, T } from './texts'
import { Skeleton } from './ui'

const MIN_LETTERS = 2 // the service finds nothing useful by one letter
const STEP = 8 // rows shown at first and added by "Show more"

/** One found code: the code, its state, the name and a link to the same code in the EIS. */
function CodeRow({ code }: { code: CatalogCode }) {
  const active = code.status === 'ACTIVE'
  const url = ktruUrl(code.code)
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
        {code.characteristics.length > 0 && (
          <div className="chips">
            {code.characteristics.slice(0, 3).map(([name, values]) => (
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

  const q = query.trim()
  useEffect(() => {
    if (q.length < MIN_LETTERS) return
    let alive = true
    const timer = window.setTimeout(() => {
      setLoading(true)
      api
        .catalog(q)
        .then((codes) => {
          if (!alive) return
          setFound(codes)
          setShown(STEP)
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
              <h2 className="section-h">{T.catalogSearch.found(list.length)}</h2>
              <span className="muted">{T.catalogSearch.activeFirst}</span>
            </div>
            <ul className="found-list">
              {list.slice(0, shown).map((code) => (
                <CodeRow key={code.code} code={code} />
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
