import { App as AntApp, ConfigProvider } from 'antd'
import ruRU from 'antd/locale/ru_RU'
import { CircleUserRound, ExternalLink, LogIn, LogOut, LucideProvider, Menu, ScanSearch, X } from 'lucide-react'
import { useMemo, useEffect, useState, type ReactNode } from 'react'
import { HealthContext, type HealthState } from './health'
import { api, ApiError, LOGGED_OUT, type Me } from './api'
import { EisMark } from './EisMark'
import { ItemScreen } from './ItemScreen'
import { JobScreen } from './JobScreen'
import { JobsScreen } from './JobsScreen'
import { Link } from './Link'
import { LoginScreen } from './LoginScreen'
import { ProfileScreen } from './ProfileScreen'
import { clearAccountViews, navigate, parseRoute, paths, usePath } from './router'
import { ConsentGate } from './ConsentSettings'
import { CookieNotice, LegalLinks, LegalScreen } from './LegalScreen'
import { T } from './texts'
import { UploadScreen } from './UploadScreen'
import { EmptyState } from './ui'

/** The KTRU catalog of the official procurement system, to check a code there. */
const EIS_CATALOG = 'https://zakupki.gov.ru/epz/ktru/start/startPage.html'

/** One poll of /api/health for the whole page: the header badge and the home banners share it. */
function HealthProvider({ children }: { children: ReactNode }) {
  const [health, setHealth] = useState<HealthState>(null)
  useEffect(() => {
    let alive = true
    let timer = 0
    const load = () =>
      api
        .health()
        .then((h) => alive && setHealth(h))
        .catch(() => alive && setHealth('offline'))
        .finally(() => {
          if (alive) timer = window.setTimeout(load, 3000)
        })
    load()
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [])
  return <HealthContext.Provider value={health}>{children}</HealthContext.Provider>
}

/** Who is logged in: undefined while asking, null when the login page is needed. A 401 from any
 * request (the session ended) sends the user back to the login page. */
function useMe() {
  const [me, setMe] = useState<Me | null | undefined>(undefined)
  useEffect(() => {
    let alive = true
    api
      .me()
      .then((found) => alive && setMe(found))
      .catch((error) => {
        // no answer at all: show the site, its own notices say that the server is unreachable
        if (!alive) return
        setMe(error instanceof ApiError && error.status === 401 ? null : { login: '', is_admin: false, is_guest: false })
      })
    const loggedOut = () => { clearAccountViews(); setMe(null) }
    window.addEventListener(LOGGED_OUT, loggedOut)
    return () => {
      alive = false
      window.removeEventListener(LOGGED_OUT, loggedOut)
    }
  }, [])
  return [me, setMe] as const
}

function UserTools({ me, onLogout, onSignIn }: { me: Me; onLogout: () => void; onSignIn: () => void }) {
  if (!me.login) return null // the server runs without accounts
  if (me.is_guest) return (
    <div className="user-tools">
      <span className="user-name" title={T.login.guestNameTitle}>
        <CircleUserRound size={18} aria-hidden="true" />
        <span className="user-login">{T.login.guestName}</span>
      </span>
      <button type="button" className="btn ghost small" onClick={onSignIn}>
        <LogIn size={16} aria-hidden="true" /> {T.login.signIn}
      </button>
    </div>
  )
  return (
    <div className="user-tools">
      <Link to={paths.profile} className="user-name" title={T.profile.open}>
        <CircleUserRound size={18} aria-hidden="true" />
        <span className="user-login">{me.login}</span>
      </Link>
      <button type="button" className="btn ghost small" onClick={onLogout}>
        <LogOut size={16} aria-hidden="true" /> {T.login.logout}
      </button>
    </div>
  )
}

/** antd tokens are read from theme.css, so the colors and sizes stay in one place. */
function readTokens() {
  const css = getComputedStyle(document.documentElement)
  const v = (name: string) => css.getPropertyValue(`--${name}`).trim()
  const px = (name: string) => parseFloat(v(name))
  const still = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  return {
    token: {
      colorPrimary: v('primary-bg'),
      colorLink: v('accent-2'),
      colorText: v('ink'),
      colorTextSecondary: v('ink-2'),
      colorTextDescription: v('ink-2'),
      colorTextPlaceholder: v('ink-3'),
      colorBgContainer: v('paper-raised'),
      colorBgElevated: v('paper-raised'),
      colorBgLayout: v('bg'),
      colorBgSpotlight: v('ink'),
      colorBorder: v('input-line'),
      colorBorderSecondary: v('line'),
      colorSplit: v('line'),
      colorSuccess: v('green'),
      colorWarning: v('amber'),
      colorError: v('red'),
      controlItemBgHover: v('accent-soft'),
      controlItemBgActive: v('accent-soft'),
      controlOutline: v('accent-soft'),
      borderRadius: px('radius-sm'),
      borderRadiusXS: px('radius-xs'),
      borderRadiusSM: px('radius-xs'),
      borderRadiusLG: px('radius-md'),
      controlHeight: px('control'),
      fontFamily: v('font'),
      fontSize: px('text'),
      motion: !still,
    },
    components: {
      // tooltips are white cards (theme.css); the arrow takes its color from these tokens
      Tooltip: { colorBgSpotlight: v('paper-raised'), colorTextLightSolid: v('ink') },
    },
  }
}

function Missing() {
  return (
    <div className="wrap section">
      <EmptyState icon={ScanSearch} title={T.app.notFound} hint={T.app.notFoundHint}>
        <Link className="btn primary" to={paths.home}>
          {T.app.home}
        </Link>
      </EmptyState>
    </div>
  )
}

function Header({
  page,
  me,
  onLogout,
  onSignIn,
}: {
  page: ReturnType<typeof parseRoute>['page']
  me: Me
  onLogout: () => void
  onSignIn: () => void
}) {
  const path = usePath()
  // The menu is open for the page it was opened on: a tap on an item changes the page and closes it
  const [openAt, setOpenAt] = useState<string | null>(null)
  const menuOpen = openAt === path

  const items = [
    { to: paths.home, label: T.app.nav.upload, on: page === 'home' },
    { to: paths.jobs, label: T.app.nav.jobs, on: page === 'jobs' || page === 'job' || page === 'item' },
    { to: paths.catalog, label: T.app.nav.catalog, on: page === 'catalog' },
  ]
  return (
    <header className="site-head">
      <div className="wrap head-in">
        <Link className="brand" to={paths.home} aria-label={T.app.home}>
          <EisMark height={38} />
          <span className="brand-title">{T.app.title}</span>
        </Link>
        <nav className="main-nav" aria-label={T.app.title}>
          {items.map((item) => (
            <Link key={item.to} to={item.to} aria-current={item.on ? 'page' : undefined}>
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="head-tools">
          <a
            className="eis-link"
            href={EIS_CATALOG}
            target="_blank"
            rel="noopener noreferrer"
            title={T.app.catalogLinkTitle}
          >
            {T.app.catalogLink} <ExternalLink size={14} aria-hidden="true" />
          </a>
          <UserTools me={me} onLogout={onLogout} onSignIn={onSignIn} />
        </div>
        <button
          type="button"
          className={`menu-btn ${menuOpen ? 'open' : ''}`}
          aria-expanded={menuOpen}
          aria-controls="mobile-menu"
          aria-label={menuOpen ? T.app.menuClose : T.app.menu}
          onClick={() => setOpenAt(menuOpen ? null : path)}
        >
          {menuOpen ? <X size={22} aria-hidden="true" /> : <Menu size={22} aria-hidden="true" />}
        </button>
      </div>
      {menuOpen && (
        <div className="mobile-menu" id="mobile-menu">
          <nav className="wrap" aria-label={T.app.title}>
            {items.map((item) => (
              <Link key={item.to} to={item.to} aria-current={item.on ? 'page' : undefined}>
                {item.label}
              </Link>
            ))}
          </nav>
          <div className="wrap mobile-tools">
            <UserTools me={me} onLogout={onLogout} onSignIn={onSignIn} />
          </div>
        </div>
      )}
    </header>
  )
}

export function App() {
  const path = usePath()
  const route = parseRoute(path)
  const config = useMemo(() => readTokens(), [])
  const [me, setMe] = useMe()
  // a guest opened the login page from the header; the guest session stays until the login
  const [signIn, setSignIn] = useState(false)
  const logout = () => {
    void api
      .logout()
      .catch(() => undefined) // the cookie is gone or the server is down: the login page anyway
      .finally(() => { clearAccountViews(); setMe(null) })
  }

  const page = () => {
    switch (route.page) {
      // the same element in both cases: the files chosen on one tab stay when the other one opens
      case 'home':
        return <UploadScreen mode="upload" />
      case 'catalog':
        return <UploadScreen mode="search" />
      case 'jobs':
        return <JobsScreen />
      case 'profile':
        return me && !me.is_guest ? <ProfileScreen me={me} onDone={setMe} /> : <Missing />
      case 'job':
        return <JobScreen key={route.jobId} jobId={route.jobId} />
      case 'item':
        return <ItemScreen key={route.itemId} jobId={route.jobId} itemId={route.itemId} />
      default:
        return <Missing />
    }
  }

  return (
    <ConfigProvider theme={config} locale={ruRU}>
      {/* every icon gets the "icon" class and one thin stroke */}
      <LucideProvider className="icon" strokeWidth={1.75}>
        <AntApp component={false}>
          <HealthProvider>
            {route.page === 'legal' && <LegalScreen document={route.document} />}
            {route.page !== 'legal' && (me === null || (me?.is_guest && signIn)) && <LoginScreen
              onBack={me ? () => setSignIn(false) : undefined}
              onDone={(found) => {
                clearAccountViews()
                // a guest who logged in keeps the place; a new visitor starts on the home page
                if (!me) navigate(paths.home)
                setSignIn(false)
                setMe(found)
              }}
            />}
            {route.page !== 'legal' && me && !(me.is_guest && signIn) && (
              <div className="site">
                <Header page={route.page} me={me} onLogout={logout} onSignIn={() => setSignIn(true)} />
                <main>{route.page === 'profile' ? page() : <ConsentGate login={me.login} needsFiles={route.page === 'home'}>{page()}</ConsentGate>}</main>
                <footer className="site-foot" aria-labelledby="feedback-title">
                  <div className="wrap foot-in">
                    <h2 id="feedback-title" className="foot-title">
                      {T.feedback.title}
                    </h2>
                    <span>{T.feedback.name}</span>
                    <span>{T.feedback.email}</span>
                    <LegalLinks compact />
                  </div>
                </footer>
              </div>
            )}
            <CookieNotice />
          </HealthProvider>
        </AntApp>
      </LucideProvider>
    </ConfigProvider>
  )
}
