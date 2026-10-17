import { ConfigProvider, theme as antTheme } from 'antd'
import ruRU from 'antd/locale/ru_RU'
import { Moon, Mountain, Sun } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type Health } from './api'
import { JobScreen } from './JobScreen'
import { Mountains } from './Mountains'
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

/** antd tokens are read from theme.css, so the colors stay in one place. */
function readTokens(theme: Theme) {
  const css = getComputedStyle(document.documentElement)
  const v = (name: string) => css.getPropertyValue(`--${name}`).trim()
  return {
    algorithm: theme === 'dark' ? antTheme.darkAlgorithm : antTheme.defaultAlgorithm,
    token: {
      colorPrimary: v('accent-strong'),
      colorText: v('text'),
      colorTextSecondary: v('text-2'),
      colorTextDescription: v('text-2'),
      colorBgContainer: v('panel-strong'),
      colorBgElevated: v('panel-solid'),
      colorBorder: v('panel-border'),
      colorBorderSecondary: v('track'),
      colorSuccess: v('high'),
      colorWarning: v('medium'),
      colorError: v('low'),
      borderRadius: 12,
      fontFamily: "'Inter', system-ui, sans-serif",
    },
    components: {
      Table: {
        headerBg: 'transparent',
        rowHoverBg: v('panel-hover'),
        rowSelectedBg: v('accent-soft'),
      },
    },
  }
}

/** Two routes: "/" and "/jobs/<id>"; plain history API, nginx serves index.html for both. */
function usePath(): [string, (path: string) => void] {
  const [path, setPath] = useState(window.location.pathname)
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname)
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  const navigate = useCallback((next: string) => {
    window.history.pushState(null, '', next)
    setPath(next)
    window.scrollTo(0, 0)
  }, [])
  return [path, navigate]
}

function HealthBadge() {
  const [health, setHealth] = useState<Health | null | 'offline'>(null)
  useEffect(() => {
    let timer = 0
    const load = () =>
      api
        .health()
        .then((h) => {
          setHealth(h)
          if (h.catalog === 'loading') timer = window.setTimeout(load, 3000)
        })
        .catch(() => setHealth('offline'))
    load()
    return () => window.clearTimeout(timer)
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
      {text} · {health.llm ? T.app.llm.on : T.app.llm.off}
    </div>
  )
}

export function App() {
  const [theme, setTheme] = useState<Theme>(() => {
    const initial = savedTheme()
    applyTheme(initial)
    return initial
  })
  const [path, navigate] = usePath()
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

  const jobId = path.match(/^\/jobs\/([\w-]+)/)?.[1]

  return (
    <ConfigProvider theme={config} locale={ruRU}>
      <Mountains className="backdrop" sky={false} sun={false} />
      <div className="app">
        <header className="header glass">
          <a
            className="brand"
            href="/"
            aria-label={T.app.home}
            onClick={(e) => {
              e.preventDefault()
              navigate('/')
            }}
          >
            <span className="round small" aria-hidden="true">
              <Mountain size={18} />
            </span>
            <span>
              <div className="brand-title">{T.app.title}</div>
              <div className="brand-sub">{T.app.subtitle}</div>
            </span>
          </a>
          <div className="header-spacer" />
          <HealthBadge />
          <button
            type="button"
            className="round ghost small"
            onClick={toggleTheme}
            aria-label={theme === 'light' ? T.app.themeDark : T.app.themeLight}
            title={theme === 'light' ? T.app.themeDark : T.app.themeLight}
          >
            {theme === 'light' ? <Moon size={17} /> : <Sun size={17} />}
          </button>
        </header>
        <main className="page" key={jobId ?? 'home'}>
          {jobId ? (
            <JobScreen jobId={jobId} onHome={() => navigate('/')} />
          ) : (
            <UploadScreen onJob={(id) => navigate(`/jobs/${id}`)} />
          )}
        </main>
      </div>
    </ConfigProvider>
  )
}
