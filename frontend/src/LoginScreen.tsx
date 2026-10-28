import { Input } from 'antd'
import { LogIn } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { api, ApiError, type Me } from './api'
import { EisMark } from './EisMark'
import { useHealth } from './health'
import { T } from './texts'

/** The only page without an account: login and password, the server keeps the session. */
export function LoginScreen({ onDone }: { onDone: (me: Me) => void }) {
  const health = useHealth()
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const noUsers = health && health !== 'offline' && health.auth === 'no_users'

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!login.trim() || !password) {
      setError(T.login.empty)
      return
    }
    setBusy(true)
    setError('')
    try {
      onDone(await api.login(login.trim(), password))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : T.errors.network)
      setPassword('')
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
        <h1 className="login-title">{T.login.title}</h1>
        <p className="login-hint">{noUsers ? T.login.noUsers : T.login.hint}</p>
        <label className="login-field">
          {T.login.login}
          <Input
            value={login}
            autoComplete="username"
            autoFocus
            maxLength={64}
            onChange={(e) => setLogin(e.target.value)}
          />
        </label>
        <label className="login-field">
          {T.login.password}
          <Input.Password
            value={password}
            autoComplete="current-password"
            maxLength={200}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && (
          <p className="login-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="btn primary login-submit" disabled={busy}>
          <LogIn size={16} aria-hidden="true" /> {busy ? T.login.busy : T.login.submit}
        </button>
      </form>
    </main>
  )
}
