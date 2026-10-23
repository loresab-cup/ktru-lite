import { App as AntApp, ConfigProvider } from 'antd'
import ruRU from 'antd/locale/ru_RU'
import { BookOpen, ListChecks, LucideProvider, UploadCloud } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api, type Health } from './api'
import logo from './assets/logo.png'
import { ItemScreen } from './ItemScreen'
import { JobScreen } from './JobScreen'
import { JobsScreen } from './JobsScreen'
import { Link } from './Link'
import { parseRoute, paths, usePath } from './router'
import { T } from './texts'
import { UploadScreen } from './UploadScreen'

/** The KTRU catalog of the official procurement system, to check a code there. */
const EIS_CATALOG = 'https://zakupki.gov.ru/epz/ktru/start/startPage.html'

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
      {text}, {T.app.llm[health.llm] ?? T.app.llm.off}
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
  const path = usePath()
  const route = parseRoute(path)
  const config = useMemo(() => readTokens(), [])

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
                    <a
                      className="cabinet"
                      href={EIS_CATALOG}
                      target="_blank"
                      rel="noopener noreferrer"
                      title={T.app.catalogLinkTitle}
                    >
                      <BookOpen size={16} aria-hidden="true" /> {T.app.catalogLink}
                    </a>
                  </div>
                </div>
              </div>
              <div className="wrap brand-row">
                <Link className="brand" to={paths.home} aria-label={T.app.home}>
                  <img className="brand-logo" src={logo} alt="" />
                  <span className="brand-name">
                    <span className="brand-title">{T.app.title}</span>
                    <span className="brand-sub">{T.app.subtitle}</span>
                  </span>
                </Link>
                <div className="brand-links">
                  <Link className="round-link" to={paths.jobs}>
                    <span className="round-icon" aria-hidden="true">
                      <ListChecks size={20} />
                    </span>
                    {T.app.links.jobs}
                  </Link>
                  <Link className="round-link" to={paths.home}>
                    <span className="round-icon" aria-hidden="true">
                      <UploadCloud size={20} />
                    </span>
                    {T.app.links.upload}
                  </Link>
                </div>
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
