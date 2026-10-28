import { Checkbox, Input, Segmented } from 'antd'
import { LogIn, UserPlus } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { api, ApiError, type Me } from './api'
import { EisMark } from './EisMark'
import { T } from './texts'
import { ConsentDocumentLink } from './ConsentSettings'
import { LegalLinks } from './LegalScreen'

type Mode = 'login' | 'register'

/** The only page without an account: log in, or register by login and password. */
export function LoginScreen({ onDone }: { onDone: (me: Me) => void }) {
  const [mode, setMode] = useState<Mode>('login')
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [privacyAccepted, setPrivacyAccepted] = useState(false)
  const [privacyVersion, setPrivacyVersion] = useState('')
  const registering = mode === 'register'
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
    setPrivacyAccepted(false)
  }

  const problem = (): string => {
    if (!login.trim() || !password) return T.login.empty
    if (registering && password !== repeat) return T.login.mismatch
    if (registering && (!privacyAccepted || !privacyVersion)) return T.privacy.required
    return ''
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const found = problem()
    if (found) {
      setError(found)
      return
    }
    setBusy(true)
    setError('')
    try {
      onDone(registering
        ? await api.register(login.trim(), password, privacyVersion)
        : await api.login(login.trim(), password))
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
            { value: 'login', label: T.login.loginTab },
            { value: 'register', label: T.login.registerTab },
          ]}
        />
        <h1 className="login-title">{registering ? T.login.registerTitle : T.login.title}</h1>
        <p className="login-hint">{registering ? T.login.registerHint : T.login.hint}</p>
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
        {registering && <div className="consent-option">
          <Checkbox checked={privacyAccepted} onChange={(event) => setPrivacyAccepted(event.target.checked)}>{T.privacy.labels.account}</Checkbox>
          <ConsentDocumentLink purpose="account" />
        </div>}
        {error && (
          <p className="login-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="btn primary login-submit" disabled={busy || (registering && (!privacyAccepted || !privacyVersion))}>
          {registering ? <UserPlus size={16} aria-hidden="true" /> : <LogIn size={16} aria-hidden="true" />}{' '}
          {busy ? T.login.busy : registering ? T.login.registerSubmit : T.login.submit}
        </button>
        <LegalLinks />
      </form>
    </main>
  )
}
