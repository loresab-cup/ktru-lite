import {
  ChevronDown,
  Pencil
} from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import {
  api,
  type CatalogCode,
  type ItemPage
} from '../api'
import { capital, T } from '../texts'
import { Confidence, KtruCode } from '../ui'
import { CodeDialog } from './CodeDialog'



/** What the catalog says about the code: from the candidates, else asked from the service. */
function useCatalogInfo(item: ItemPage): CatalogCode | null | undefined {
  const code = item.ktru_code
  const known = item.candidates.find((c) => c.code === code)
  const [fetched, setFetched] = useState<{ code: string; info: CatalogCode | null } | null>(null)
  useEffect(() => {
    if(!code || known) return
    let alive = true
    api
      .catalog(code)
      .then((list) => alive && setFetched({ code, info: list.find((c) => c.code === code) ?? null }))
      .catch(() => alive && setFetched({ code, info: null }))
    return () => {
      alive = false
    }
  }, [code, known])
  if(!code) return null
  if(known) return known
  return fetched?.code === code ? fetched.info : undefined // undefined: still loading
}


function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="fact">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  )
}


export function GeneralTab({ item, busy, onChoose }: {
  item: ItemPage
  busy: boolean
  onChoose: (code: string) => Promise<boolean>
}) {
  const info = useCatalogInfo(item)
  const [dialog, setDialog] = useState(false)
  const [chars, setChars] = useState(false) // what the code allows is a long list: closed at first
  const active = info?.status === 'ACTIVE'
  return (
    <>
      <section className="card tab-card">
        <h2 className="tab-h">{T.item.general}</h2>
        <dl className="facts-grid">
          <Fact label={T.item.codeLabel}>
            <div className="code-edit">
              {item.ktru_code ? <KtruCode code={item.ktru_code} /> : <span className="muted">{T.drawer.noCode}</span>}
              <button type="button" className="btn small" onClick={() => setDialog(true)}>
                <Pencil size={16} aria-hidden="true" /> {T.drawer.enterCode}
              </button>
            </div>
          </Fact>
          <Fact label={T.results.confidence}>
            <span className="fact-conf">
              <Confidence value={item.confidence} zone={item.zone} />
            </span>
          </Fact>
          <Fact label={T.item.catalogName}>
            <span className="sentence">{info?.name ?? item.ktru_name ?? T.item.none}</span>
          </Fact>
          {item.summary && <Fact label={T.item.why}>{item.summary}</Fact>}
          <Fact label={T.item.okpd2}>{info?.okpd2_code || T.item.none}</Fact>
          <Fact label={T.item.codeState}>
            {info ? (
              <span className={`status ${active ? 'status-accepted' : 'status-failed'}`}>
                <span className="status-dot" aria-hidden="true" />
                {capital(T.codeStatus[info.status] ?? info.status)}
              </span>
            ) : (
              T.item.none
            )}
          </Fact>
        </dl>
        {info === null && item.ktru_code && <p className="muted">{T.item.noCatalog}</p>}
      </section>
      {info && info.characteristics.length > 0 && (
        <section className="card tab-card fold">
          <button
            type="button"
            className="fold-head"
            aria-expanded={chars}
            onClick={() => setChars(!chars)}
          >
            {T.item.catalogChars(info.characteristics.length)}
            <ChevronDown size={18} aria-hidden="true" className={chars ? 'flip' : ''} />
          </button>
          {chars && (
            <dl className="facts-grid thirds">
              {info.characteristics.map(([name, values]) => (
                <Fact key={name} label={name}>
                  {values.join('; ')}
                </Fact>
              ))}
            </dl>
          )}
        </section>
      )}
      <CodeDialog open={dialog} busy={busy} onClose={() => setDialog(false)} onSave={onChoose} />
    </>
  )
}
