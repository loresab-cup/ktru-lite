import { Input } from 'antd'
import { CircleUserRound, KeyRound, Save, UserRound } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { api, ApiError, LOGGED_OUT, type Me } from './api'
import { Link } from './Link'
import { paths } from './router'
import { T } from './texts'
import { ConsentSettings } from './ConsentSettings'

/** Independent forms keep a password change separate from an account-name change. */
export function ProfileScreen({ me, onDone }: { me: Me; onDone: (me: Me) => void }) {
  const [login, setLogin] = useState(me.login)
  const [loginPassword, setLoginPassword] = useState('')
  const [currentPassword, setCurrentPassword] = useState('')
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [busy, setBusy] = useState<'login' | 'password' | null>(null)
  const [notice, setNotice] = useState<{ kind: 'error' | 'success'; text: string } | null>(null)

  const submit = async (event: FormEvent, form: 'login' | 'password') => {
    event.preventDefault()
    if (busy) return
    setNotice(null)
    const current = form === 'login' ? loginPassword : currentPassword
    let problem = ''
    if (!current || (form === 'login' ? !login.trim() : !password || !repeat)) problem = T.profile.empty
    else if (form === 'login' && !/^[a-z0-9._-]{3,32}$/.test(login.trim().toLowerCase())) problem = T.profile.invalidLogin
    else if (form === 'password' && password.length < 8) problem = T.profile.shortPassword
    else if (form === 'password' && password !== repeat) problem = T.login.mismatch
    if (problem) {
      setNotice({ kind: 'error', text: problem })
      return
    }
    setBusy(form)
    try {
      const updated = await api.updateProfile(current, form === 'login' ? { login: login.trim() } : { password })
      onDone(updated)
      if (form === 'login') setLogin(updated.login)
      setNotice({ kind: 'success', text: form === 'login' ? T.profile.savedLogin : T.profile.savedPassword })
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) window.dispatchEvent(new Event(LOGGED_OUT))
      setNotice({ kind: 'error', text: error instanceof ApiError ? error.message : T.errors.network })
    } finally {
      setLoginPassword('')
      setCurrentPassword('')
      setPassword('')
      setRepeat('')
      setBusy(null)
    }
  }

  if (!me.login) return <div className="wrap profile-page"><p>{T.profile.disabled}</p><Link to={paths.home}>{T.profile.back}</Link></div>

  return (
    <div className="wrap profile-page">
      <div className="page-head">
        <h1 className="page-title">{T.profile.title}</h1>
        <p className="profile-lead">{T.profile.lead}</p>
      </div>
      <div className="profile-account card">
        <CircleUserRound size={36} aria-hidden="true" />
        <div><span className="muted">{T.profile.account}</span><strong>{me.login}</strong></div>
      </div>
      {notice && <p className={`profile-notice ${notice.kind}`} role={notice.kind === 'error' ? 'alert' : 'status'}>{notice.text}</p>}
      <div className="profile-grid">
        <form className="card profile-form" noValidate onSubmit={(event) => void submit(event, 'login')}>
          <h2><UserRound size={22} aria-hidden="true" />{T.profile.loginTitle}</h2>
          <p className="profile-hint">{T.profile.loginHint}</p>
          <label className="login-field">{T.profile.newLogin}
            <Input name="username" autoComplete="username" value={login} maxLength={32} required onChange={(event) => setLogin(event.target.value)} />
            <span className="login-rule">{T.login.loginRule}</span>
          </label>
          <label className="login-field">{T.profile.currentPassword}
            <Input.Password name="current-password" autoComplete="current-password" value={loginPassword} maxLength={200} required onChange={(event) => setLoginPassword(event.target.value)} />
          </label>
          <button className="btn primary" type="submit" disabled={busy !== null || login.trim().toLowerCase() === me.login}><Save size={16} aria-hidden="true" />{busy === 'login' ? T.login.busy : T.profile.loginSubmit}</button>
        </form>
        <form className="card profile-form" noValidate onSubmit={(event) => void submit(event, 'password')}>
          <h2><KeyRound size={22} aria-hidden="true" />{T.profile.passwordTitle}</h2>
          <p className="profile-hint">{T.profile.passwordHint}</p>
          <label className="login-field">{T.profile.currentPassword}
            <Input.Password name="current-password" autoComplete="current-password" value={currentPassword} maxLength={200} required onChange={(event) => setCurrentPassword(event.target.value)} />
          </label>
          <label className="login-field">{T.profile.newPassword}
            <Input.Password name="new-password" autoComplete="new-password" value={password} maxLength={200} required onChange={(event) => setPassword(event.target.value)} />
            <span className="login-rule">{T.login.passwordRule}</span>
          </label>
          <label className="login-field">{T.profile.repeatPassword}
            <Input.Password name="repeat-password" autoComplete="new-password" value={repeat} maxLength={200} required onChange={(event) => setRepeat(event.target.value)} />
          </label>
          <button className="btn primary" type="submit" disabled={busy !== null}><Save size={16} aria-hidden="true" />{busy === 'password' ? T.login.busy : T.profile.passwordSubmit}</button>
        </form>
      </div>
      <p className="profile-session"><KeyRound size={16} aria-hidden="true" />{T.profile.sessionHint}</p>
      <ConsentSettings />
    </div>
  )
}
