import { ConfigProvider, theme as antTheme } from 'antd'
import ruRU from 'antd/locale/ru_RU'
import { FileStack, Moon, Plus, Sun } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type Health, type Job } from './api'
import { Deco } from './Art'
import { JobScreen, type PageInfo } from './JobScreen'
import { T } from './texts'
import { Ring } from './ui'
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
      colorBgContainer: v('surface'),
      colorBgElevated: v('surface-solid'), // drawers and menus stay opaque over the glass
      colorBorder: v('border'),
      colorBorderSecondary: v('border'),
      colorSuccess: v('high'),
      colorWarning: v('medium'),
      colorError: v('low'),
      borderRadius: 10,
      fontFamily: "'Inter', system-ui, sans-serif",
    },
    components: {
      Table: {
        headerBg: v('surface-2'),
        headerColor: v('text-2'),
        rowHoverBg: v('surface-2'),
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

/** Recent jobs; refreshed on every page change and every 3 s while one of them runs. */
function useRecentJobs(path: string) {
  const [jobs, setJobs] = useState<Job[] | null>(null)
  useEffect(() => {
    let alive = true
    let timer = 0
    const load = () =>
      api
        .jobs()
        .then((list) => {
          if (!alive) return
          setJobs(list)
          if (list.some((job) => job.status === 'queued' || job.status === 'processing')) {
            timer = window.setTimeout(load, 3000)
          }
        })
        .catch(() => alive && setJobs([]))
    load()
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [path])
  return jobs
}

function Sidebar({ jobs, jobId, ring, navigate }: {
  jobs: Job[] | null
  jobId?: string
  ring?: PageInfo['ring']
  navigate: (path: string) => void
}) {
  return (
    <nav className="sidebar" aria-label={T.app.title}>
      <a
        className="brand"
        href="/"
        aria-label={T.app.home}
        onClick={(e) => {
          e.preventDefault()
          navigate('/')
        }}
      >
        <span className="brand-mark" aria-hidden="true">
          <FileStack size={20} />
        </span>
        <span>
          <div className="brand-title">{T.app.title}</div>
          <div className="brand-sub">{T.app.subtitle}</div>
        </span>
      </a>
      <button type="button" className="side-button" onClick={() => navigate('/')}>
        <Plus size={17} aria-hidden="true" /> {T.app.newFile}
      </button>
      <div className="side-title">{T.app.recent}</div>
      <div className="side-jobs">
        {jobs?.length === 0 && <div className="side-empty">{T.app.recentEmpty}</div>}
        {jobs?.map((job) => (
          <button
            key={job.id}
            type="button"
            className="side-job"
            aria-current={job.id === jobId ? 'page' : undefined}
            onClick={() => navigate(`/jobs/${job.id}`)}
          >
            <span className="side-bullet" aria-hidden="true" />
            <span>
              <div className="side-job-name" title={job.filename}>
                {job.filename}
              </div>
              <div className="side-job-meta num">
                {T.upload.positions(job.total)} · {T.jobStatus[job.status] ?? job.status}
              </div>
            </span>
          </button>
        ))}
      </div>
      {ring && (
        <div className="side-ring">
          <Ring percent={ring.percent} label={ring.label} size={150} />
        </div>
      )}
    </nav>
  )
}

export function App() {
  const [theme, setTheme] = useState<Theme>(() => {
    const initial = savedTheme()
    applyTheme(initial)
    return initial
  })
  const [path, navigate] = usePath()
  // Page info belongs to the path it was reported for: another page starts with none
  const [reported, setReported] = useState<PageInfo & { path?: string }>({})
  const jobs = useRecentJobs(path)
  const config = useMemo(() => readTokens(theme), [theme])
  const jobId = path.match(/^\/jobs\/([\w-]+)/)?.[1]

  const info: PageInfo = reported.path === path ? reported : {}
  const onInfo = useCallback((next: PageInfo) => setReported({ ...next, path }), [path])

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

  return (
    <ConfigProvider theme={config} locale={ruRU}>
      <div className="shell">
        <Sidebar jobs={jobs} jobId={jobId} ring={info.ring} navigate={navigate} />
        <div className="main">
          <Deco />
          <header className="topbar">
            <h1>{jobId ? (info.title ?? T.progress.pageTitle) : T.app.pageUpload}</h1>
            <div className="topbar-spacer" />
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
              <JobScreen jobId={jobId} onHome={() => navigate('/')} onInfo={onInfo} />
            ) : (
              <UploadScreen jobs={jobs} onJob={(id) => navigate(`/jobs/${id}`)} />
            )}
          </main>
        </div>
      </div>
    </ConfigProvider>
  )
}
