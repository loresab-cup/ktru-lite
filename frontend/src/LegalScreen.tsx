import { useEffect, useState } from 'react'
import { api, type LegalInfo } from './api'
import { Link } from './Link'
import { paths } from './router'
import { T } from './texts'

export type LegalDocument = 'privacy' | 'cookies' | 'account' | 'files' | 'improvement'

export function LegalLinks({ compact = false }: { compact?: boolean }) {
  if (compact) return <Link className="legal-summary" to={paths.privacy}>{T.privacy.documents}</Link>
  return <nav className="legal-links" aria-label={T.privacy.policy}>
    <Link to={paths.privacy}>{T.privacy.policy}</Link>
    <Link to={paths.cookies}>{T.privacy.cookies}</Link>
    <Link to={paths.consent('files')}>{T.privacy.filesLink}</Link>
  </nav>
}

/** Public documents remain readable before registration and without any cookies. */
export function LegalScreen({ document }: { document: LegalDocument }) {
  const [info, setInfo] = useState<LegalInfo | null>(null)
  const [error, setError] = useState(false)
  useEffect(() => {
    let alive = true
    api.legal().then((found) => { if (alive) setInfo(found) }).catch(() => { if (alive) setError(true) })
    return () => { alive = false }
  }, [])
  const title = document === 'privacy' ? T.privacy.policy : document === 'cookies' ? T.privacy.cookies : T.privacy.labels[document]
  const sections = document === 'privacy' ? T.privacy.policySections : document === 'cookies' ? T.privacy.cookieSections : null
  const paragraphs = document === 'account' ? T.privacy.accountText : document === 'files' ? T.privacy.filesText : T.privacy.improvementText
  return <main className="wrap legal-page">
    <Link to={paths.home}>{T.privacy.back}</Link>
    <h1>{title}</h1>
    {!info ? <p role="status">{error ? T.privacy.loadError : T.privacy.loading}</p> : <>
      <p className="muted">{T.privacy.version(info.version)}</p>
      <div className="card legal-contact">
        <p>{T.privacy.operator(info.operator)}</p>
        {info.address && <p>{T.privacy.address(info.address)}</p>}
        <p>{T.privacy.contact}<a href={`mailto:${info.email}`}>{info.email}</a></p>
      </div>
      {sections ? sections.map(([heading, body]) => <section key={heading}><h2>{heading}</h2><p>{body}</p></section>) : paragraphs.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}
      {(document === 'privacy' || document === 'files') && <p>{T.privacy.services(info.providers)}</p>}
      {document === 'cookies' && <p>{T.privacy.session(info.session_hours)}</p>}
    </>}
    <LegalLinks />
  </main>
}

const NOTICE_KEY = 'ktru:necessary-cookies:v1'

export function CookieNotice() {
  const [visible, setVisible] = useState(() => {
    try { return localStorage.getItem(NOTICE_KEY) !== 'necessary' } catch { return true }
  })
  if (!visible) return null
  return <section className="cookie-notice card" aria-label={T.privacy.cookies}>
    <div><p>{T.privacy.cookieNotice}</p><Link to={paths.cookies}>{T.privacy.cookieMore}</Link></div>
    <button className="btn primary small" type="button" onClick={() => {
      try { localStorage.setItem(NOTICE_KEY, 'necessary') } catch { /* Browser storage is optional. */ }
      setVisible(false)
    }}>{T.privacy.cookieAck}</button>
  </section>
}
