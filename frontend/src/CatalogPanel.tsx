import { App as AntApp, Checkbox, Input, Switch } from 'antd'
import { ChevronDown, ExternalLink, Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, ApiError, ktruUrl, type CatalogCode } from './api'
import { capital, T } from './texts'
import { Skeleton } from './ui'

const MIN_LETTERS = 2 // the service finds nothing useful by one letter
const STEP = 20 // rows shown at first and added by "Show more"
const LIMIT = 500 // codes asked for at once: past that the query is worth narrowing
const MAIN = 3 // characteristics always shown, the rest are under the fold

type Chars = CatalogCode['characteristics']

/** For each name met more than once in the answer, the characteristics whose values differ
 *  between its codes, with the number of different values: seventeen «Молоко питьевое» are told
 *  apart by «Вид молока» and the like. */
function differing(codes: CatalogCode[]): Map<string, Map<string, number>> {
  const byName = new Map<string, CatalogCode[]>()
  for (const code of codes) byName.set(code.name, [...(byName.get(code.name) ?? []), code])
  const result = new Map<string, Map<string, number>>()
  for (const [name, group] of byName) {
    if (group.length < 2) continue
    const maps = group.map((code) => new Map(code.characteristics.map(([n, v]) => [n, v.join('; ')])))
    const names = new Set(maps.flatMap((m) => [...m.keys()]))
    const counts = [...names].map((n) => [n, new Set(maps.map((m) => m.get(n))).size] as const)
    result.set(name, new Map(counts.filter(([, count]) => count > 1)))
  }
  return result
}

function ChipList({ chars }: { chars: Chars }) {
  return chars.map(([name, values]) => (
    <span key={name} className="chip">
      {name}: {values.join('; ')}
    </span>
  ))
}

/** One found code: the code, its state, the name, the characteristics that tell it from codes
 *  of the same name (or its first ones), the rest folded as in the list of a specification,
 *  and a link to the same code in the EIS. */
function CodeRow({ code, differs, open, onToggle }: {
  code: CatalogCode
  differs?: Map<string, number>
  open: boolean
  onToggle: () => void
}) {
  const active = code.status === 'ACTIVE'
  const url = ktruUrl(code.code)
  // the ones with the most different values tell the codes apart best, so they go first
  const differ = code.characteristics
    .filter(([name]) => differs?.has(name))
    .sort(([a], [b]) => (differs?.get(b) ?? 0) - (differs?.get(a) ?? 0))
  const main = (differ.length ? differ : code.characteristics).slice(0, MAIN)
  const rest = code.characteristics.filter((c) => !main.includes(c))
  return (
    <li className={`found-row ${rest.length ? 'has-fold' : ''}`}>
      <div className="found-body">
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
          {main.length > 0 && (
            <div className="chips">
              {differ.length > 0 && <span className="muted found-differs">{T.drawer.differs}</span>}
              <ChipList chars={main} />
            </div>
          )}
        </div>
        {url && (
          <a className="btn small" href={url} target="_blank" rel="noopener noreferrer" title={T.results.openEis}>
            {T.item.openEis} <ExternalLink size={15} aria-hidden="true" />
          </a>
        )}
      </div>
      {rest.length > 0 && (
        <button type="button" className="found-more" aria-expanded={open} onClick={onToggle}>
          {T.catalogSearch.restChars}
          <ChevronDown size={18} aria-hidden="true" className={open ? 'flip' : ''} />
        </button>
      )}
      {rest.length > 0 && open && (
        <div className="chips found-rest">
          <ChipList chars={rest} />
        </div>
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
  const differs = differing(list)
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
                  differs={differs.get(code.name)}
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
