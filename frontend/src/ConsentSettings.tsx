import { Checkbox } from 'antd'
import { useEffect, useState } from 'react'
import { api, ApiError, type ConsentPurpose, type Consents } from './api'
import { paths } from './router'
import { T } from './texts'

const PURPOSES: ConsentPurpose[] = ['account', 'files', 'improvement']

export function ConsentDocumentLink({ purpose }: { purpose: ConsentPurpose }) {
  return <a className="consent-document" href={paths.consent(purpose)} target="_blank" rel="noopener noreferrer">{T.privacy.read}</a>
}

function message(error: unknown) { return error instanceof ApiError ? error.message : T.privacy.loadError }

export function ConsentSettings() {
  const [state, setState] = useState<Consents | null>(null)
  const [draft, setDraft] = useState<Consents | null>(null)
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let alive = true
    api.consents().then((found) => { if (alive) { setState(found); setDraft(found) } }).catch((e) => { if (alive) setNotice(message(e)) })
    return () => { alive = false }
  }, [])
  const save = async () => {
    if (!state || !draft) return
    setBusy(true)
    setNotice('')
    try {
      let latest = state
      for (const purpose of PURPOSES) if (draft[purpose] !== latest[purpose]) latest = await api.setConsent(purpose, draft[purpose], state.version)
      setState(latest); setDraft(latest); setNotice(T.privacy.saved)
    } catch (e) {
      setNotice(message(e))
      try { const latest = await api.consents(); setState(latest); setDraft(latest) } catch { /* Keep the error and allow retry. */ }
    } finally { setBusy(false) }
  }
  return <section className="card consent-card profile-consents">
    <h2>{T.privacy.settings}</h2><p>{T.privacy.settingsHint}</p>
    {!draft ? <p role="status">{notice || T.privacy.loading}</p> : PURPOSES.map((purpose) => <div key={purpose} className="consent-option">
      <Checkbox checked={draft[purpose]} disabled={busy || (purpose !== 'account' && !draft.account) || (purpose === 'improvement' && !draft.files)} onChange={(event) => {
        const next = { ...draft, [purpose]: event.target.checked }
        if (!next.account) { next.files = false; next.improvement = false }
        if (!next.files) next.improvement = false
        setDraft(next)
      }}>{T.privacy.labels[purpose]}</Checkbox><ConsentDocumentLink purpose={purpose} />
      {purpose === 'improvement' && <p className="muted">{T.privacy.optional}</p>}
    </div>)}
    {notice && draft && <p role="status">{notice}</p>}
    <button type="button" className="btn primary" disabled={busy || !draft} onClick={() => void save()}>{busy ? T.login.busy : T.privacy.save}</button>
  </section>
}
