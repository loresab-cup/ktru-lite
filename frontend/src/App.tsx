import { App as AntApp, ConfigProvider, theme as antTheme } from 'antd'
import ruRU from 'antd/locale/ru_RU'
import { FileStack, LucideProvider, Moon, Plus, Sun } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api, type Health } from './api'
import { ItemScreen } from './ItemScreen'
import { JobScreen } from './JobScreen'
import { JobsScreen } from './JobsScreen'
import { Link } from './Link'
import { parseRoute, paths, usePath } from './router'
import { T } from './texts'
import { UploadScreen } from './UploadScreen'

type Theme = 'light' | 'dark'
const THEME_KEY = 'ktru-theme'

function savedTheme(): Theme {
  try {
    const value = localStorage.getItem(THEME_KEY)
    if (value === 'light' || value === 'dark') return value
  } catch {
    // storage is blocked: fall back to the system preference
  }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme
}

/** antd tokens are read from theme.css, so the colors and sizes stay in one place. */
function readTokens(theme: Theme) {
  const css = getComputedStyle(document.documentElement)
  const v = (name: string) => css.getPropertyValue(`--${name}`).trim()
  const px = (name: string) => parseFloat(v(name))
  const still = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  return {
    algorithm: theme === 'dark' ? antTheme.darkAlgorithm : antTheme.defaultAlgorithm,
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
      colorBorder: v('line-strong'),
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
      borderRadiusLG: px('radius'),
      controlHeight: px('control'),
      fontFamily: v('font'),
      fontSize: px('text'),
      motion: !still,
    },
    components: {
      Table: {
        headerBg: 'transparent',
        headerColor: v('ink-2'),
        headerSplitColor: 'transparent',
        rowHoverBg: v('accent-soft'),
        borderColor: v('line'),
      },
    },
  }
}

function HealthBadge() {
  const [health, setHealth] = useState<Health | null | 'offline'>(null)
  useEffect(() => {
    let alive = true
    let timer = 0
    const load = () =>
      api
        .health()
        .then((h) => {
          if (!alive) return
          setHealth(h)
        })
        .catch(() => { if (alive) setHealth('offline') })
        .finally(() => { if (alive) timer = window.setTimeout(load, 3000) })
    load()
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [])
  if (!health) return null
  if (health === 'offline') return <div className="health">{T.app.catalog.offline}</div>
  const tone = health.catalog === 'ready' ? 'high' : health.catalog === 'loading' ? 'medium' : 'low'
  const text =
    health.catalog === 'ready'
      ? T.app.catalog.ready(health.codes)
      : health.catalog === 'loading'
        ? T.app.catalog.loading
        : T.app.catalog.error
  return (
    <div className="health num">
      <span className={`dot ${tone}`} aria-hidden="true" />
      {text} · {T.app.llm[health.llm] ?? T.app.llm.off}
    </div>
  )
}

function Missing() {
  return (
    <section className="band">
      <div className="wrap">
        <h1 className="page-title">{T.app.notFound}</h1>
        <p className="lead">{T.app.notFoundHint}</p>
        <Link className="btn primary" to={paths.home}>
          {T.app.home}
        </Link>
      </div>
    </section>
  )
}

export function App() {
  const [theme, setTheme] = useState<Theme>(() => {
    const initial = savedTheme()
    applyTheme(initial)
    return initial
  })
  const path = usePath()
  const route = parseRoute(path)
  const config = useMemo(() => readTokens(theme), [theme])

  const toggleTheme = () => {
    const next = theme === 'light' ? 'dark' : 'light'
    applyTheme(next) // before render: readTokens sees the new variables
    setTheme(next)
    try {
      localStorage.setItem(THEME_KEY, next)
    } catch {
      // not saved: the theme still works for this visit
    }
  }

  const inJobs = route.page === 'jobs' || route.page === 'job' || route.page === 'item'
  const page = () => {
    switch (route.page) {
      case 'home':
        return <UploadScreen />
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
          <div className="site">
            <header className="site-head">
              <div className="top-strip">
                <div className="wrap top-strip-in">
                  <span className="top-note">{T.app.topLine}</span>
                  <div className="top-tools">
                    <HealthBadge />
                    <button
                      type="button"
                      className="icon-btn"
                      onClick={toggleTheme}
                      aria-label={theme === 'light' ? T.app.themeDark : T.app.themeLight}
                      title={theme === 'light' ? T.app.themeDark : T.app.themeLight}
                    >
                      {theme === 'light' ? <Moon size={18} /> : <Sun size={18} />}
                    </button>
                    <Link className="cabinet" to={paths.home}>
                      <Plus size={16} aria-hidden="true" /> {T.app.newFile}
                    </Link>
                  </div>
                </div>
              </div>
              <div className="wrap brand-row">
                <Link className="brand" to={paths.home} aria-label={T.app.home}>
                  <span className="brand-mark" aria-hidden="true">
                    <FileStack size={26} />
                  </span>
                  <span>
                    <span className="brand-title">{T.app.title}</span>
                    <span className="brand-sub">{T.app.subtitle}</span>
                  </span>
                </Link>
              </div>
              <div className="nav-line">
                <nav className="wrap main-nav" aria-label={T.app.title}>
                  <Link to={paths.home} aria-current={route.page === 'home' ? 'page' : undefined}>
                    {T.app.nav.upload}
                  </Link>
                  <Link to={paths.jobs} aria-current={inJobs ? 'page' : undefined}>
                    {T.app.nav.jobs}
                  </Link>
                </nav>
              </div>
            </header>
            <main>{page()}</main>
            <footer className="site-foot">
              <div className="wrap">{T.app.subtitle}</div>
            </footer>
          </div>
        </AntApp>
      </LucideProvider>
    </ConfigProvider>
  )
}
