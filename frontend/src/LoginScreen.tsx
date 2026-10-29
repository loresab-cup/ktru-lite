import { Checkbox, Input, Segmented } from 'antd'
import { ArrowRight, LogIn, UserPlus } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { api, ApiError, type Me } from './api'
import { EisMark } from './EisMark'
import { T } from './texts'
import { paths } from './router'

type Mode = 'guest' | 'login' | 'register'

/** The first page of a visitor: work without an account, log in, or register. Every way in
 * takes one checkbox: the privacy policy with the files consent (the server writes each purpose
 * as its own event), so the site asks nothing more before the first upload. onBack is given to
 * a guest who opened it from the header: the guest tab then returns to the site with the same
 * files instead of starting a new guest. */
export function LoginScreen({ onDone, onBack }: { onDone: (me: Me) => void; onBack?: () => void }) {
  const [mode, setMode] = useState<Mode>(onBack ? 'login' : 'guest')
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [accepted, setAccepted] = useState(false)
  const [privacyVersion, setPrivacyVersion] = useState('')
  const registering = mode === 'register'
  const guest = mode === 'guest'
  const back = guest && onBack !== undefined // the guest already accepted the policy
  useEffect(() => {
    let alive = true
    api.legal().then((info) => { if (alive) setPrivacyVersion(info.version) }).catch(() => { if (alive) setError(T.privacy.loadError) })
    return () => { alive = false }
  }, [])

  const switchTo = (next: Mode) => {
    setMode(next)
    setError('')
    setPassword('')
    setRepeat('')
  }

  const problem = (): string => {
    if (back) return ''
    if (!guest && (!login.trim() || !password)) return T.login.empty
    if (registering && password !== repeat) return T.login.mismatch
    if (!accepted || !privacyVersion) return T.privacy.entryRequired
    return ''
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const found = problem()
    if (found) {
      setError(found)
      return
    }
    if (back) {
      onBack?.()
      return
    }
    setBusy(true)
    setError('')
    try {
      onDone(guest ? await api.guest(privacyVersion) : registering
        ? await api.register(login.trim(), password, privacyVersion)
        : await api.login(login.trim(), password, privacyVersion))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : T.errors.network)
      setPassword('')
      setRepeat('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="login-page">
      <form className="card login-card" onSubmit={(e) => void submit(e)} noValidate>
        <div className="login-brand">
          <EisMark height={38} />
          <span className="brand-title">{T.app.title}</span>
        </div>
        <Segmented<Mode>
          block
          aria-label={T.login.modeLabel}
          value={mode}
          onChange={switchTo}
          options={[
            { value: 'guest', label: T.login.guestTab },
            { value: 'login', label: T.login.loginTab },
            { value: 'register', label: T.login.registerTab },
          ]}
        />
        <h1 className="login-title">{guest ? T.login.guestTitle : registering ? T.login.registerTitle : T.login.title}</h1>
        <p className="login-hint">{guest ? (back ? T.login.guestBackHint : T.login.guestHint) : registering ? T.login.registerHint : T.login.hint}</p>
        {!guest && <>
          <label className="login-field">
            {T.login.login}
            <Input
              value={login}
              autoComplete="username"
              autoFocus
              maxLength={64}
              onChange={(e) => setLogin(e.target.value)}
            />
            {registering && <span className="login-rule">{T.login.loginRule}</span>}
          </label>
          <label className="login-field">
            {T.login.password}
            <Input.Password
              value={password}
              autoComplete={registering ? 'new-password' : 'current-password'}
              maxLength={200}
              onChange={(e) => setPassword(e.target.value)}
            />
            {registering && <span className="login-rule">{T.login.passwordRule}</span>}
          </label>
          {registering && (
            <label className="login-field">
              {T.login.repeat}
              <Input.Password
                value={repeat}
                autoComplete="new-password"
                maxLength={200}
                onChange={(e) => setRepeat(e.target.value)}
              />
            </label>
          )}
        </>}
        {!back && (
          <Checkbox className="entry-consent" checked={accepted} onChange={(event) => setAccepted(event.target.checked)}>
            {T.privacy.entryAccept}{' '}
            <a href={paths.privacy} target="_blank" rel="noopener noreferrer">{T.privacy.entryPolicy}</a>{' '}
            {T.privacy.entryAnd}{' '}
            <a href={paths.consent('files')} target="_blank" rel="noopener noreferrer">{T.privacy.entryFiles}</a>
          </Checkbox>
        )}
        {error && (
          <p className="login-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="btn primary login-submit" disabled={busy || (!back && (!accepted || !privacyVersion))}>
          {guest ? <ArrowRight size={16} aria-hidden="true" /> : registering ? <UserPlus size={16} aria-hidden="true" /> : <LogIn size={16} aria-hidden="true" />}{' '}
          {busy ? T.login.busy : guest ? (back ? T.login.guestBack : T.login.guestSubmit) : registering ? T.login.registerSubmit : T.login.submit}
        </button>
      </form>
    </main>
  )
}
