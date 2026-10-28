import { App as AntApp, ConfigProvider } from 'antd'
import ruRU from 'antd/locale/ru_RU'
import { ExternalLink, LogOut, LucideProvider, Menu, ScanSearch, X } from 'lucide-react'
import { useMemo, useEffect, useState, type ReactNode } from 'react'
import { HealthContext, useHealth, type HealthState } from './health'
import { api, ApiError, LOGGED_OUT, type Me } from './api'
import { EisMark } from './EisMark'
import { ItemScreen } from './ItemScreen'
import { JobScreen } from './JobScreen'
import { JobsScreen } from './JobsScreen'
import { Link } from './Link'
import { LoginScreen } from './LoginScreen'
import { parseRoute, paths, usePath } from './router'
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
        setMe(error instanceof ApiError && error.status === 401 ? null : { login: '', is_admin: false })
      })
    const loggedOut = () => setMe(null)
    window.addEventListener(LOGGED_OUT, loggedOut)
    return () => {
      alive = false
      window.removeEventListener(LOGGED_OUT, loggedOut)
    }
  }, [])
  return [me, setMe] as const
}

function UserTools({ me, onLogout }: { me: Me; onLogout: () => void }) {
  if (!me.login) return null // the server runs without accounts
  return (
    <div className="user-tools">
      <span className="user-name" title={T.login.user(me.login)}>
        {me.login}
      </span>
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

/** The state of the service in the header: the catalog and whether it answers at all. */
function HealthBadge() {
  const health = useHealth()
  if (!health) return null
  if (health === 'offline') {
    return (
      <div className="health low">
        <span className="dot" aria-hidden="true" />
        {T.app.catalog.offline}
      </div>
    )
  }
  const tone = health.catalog === 'ready' ? 'high' : health.catalog === 'loading' ? 'medium' : 'low'
  const text =
    health.catalog === 'ready'
      ? T.app.catalog.ready(health.codes)
      : health.catalog === 'loading'
        ? T.app.catalog.loading
        : T.app.catalog.error
  return (
    <div className={`health num ${tone}`}>
      <span className="dot" aria-hidden="true" />
      {text}
    </div>
  )
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
}: {
  page: ReturnType<typeof parseRoute>['page']
  me: Me
  onLogout: () => void
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
          <HealthBadge />
          <a
            className="eis-link"
            href={EIS_CATALOG}
            target="_blank"
            rel="noopener noreferrer"
            title={T.app.catalogLinkTitle}
          >
            {T.app.catalogLink} <ExternalLink size={14} aria-hidden="true" />
          </a>
          <UserTools me={me} onLogout={onLogout} />
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
            <HealthBadge />
            <UserTools me={me} onLogout={onLogout} />
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
  const logout = () => {
    void api
      .logout()
      .catch(() => undefined) // the cookie is gone or the server is down: the login page anyway
      .finally(() => setMe(null))
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
            {me === null && <LoginScreen onDone={setMe} />}
            {me && (
              <div className="site">
                <Header page={route.page} me={me} onLogout={logout} />
                <main>{page()}</main>
                <footer className="site-foot" aria-labelledby="feedback-title">
                  <div className="wrap foot-in">
                    <h2 id="feedback-title" className="foot-title">
                      {T.feedback.title}
                    </h2>
                    <span>{T.feedback.name}</span>
                    <a href={`mailto:${T.feedback.email}`}>{T.feedback.email}</a>
                  </div>
                </footer>
              </div>
            )}
          </HealthProvider>
        </AntApp>
      </LucideProvider>
    </ConfigProvider>
  )
}
