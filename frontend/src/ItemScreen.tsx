import { App as AntApp } from 'antd'
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  CheckCheck,
  ChevronRight,
  CopyCheck,
  ExternalLink,
  ScanSearch,
  Undo2
} from 'lucide-react'
import { useEffect, useState } from 'react'
import {
  api,
  ApiError,
  ktruUrl,
  type AttributesInput,
  type ItemPage,
  type Job
} from './api'
import { CandidatesTab } from './item/Candidates'
import { Link } from './Link'
import { navigate, paths, rememberItem } from './router'
import { T } from './texts'
import { Confidence, EmptyState, RawText, Skeleton, StatusChip } from './ui'

import { SourceTab } from './item/Attributes'

import { GeneralTab } from './item/Details'



/** Keys of the review mode work everywhere on the page except fields, buttons and links. */
function isTyping(target: EventTarget | null) {
  return (
    target instanceof HTMLElement &&
    target.closest('input, textarea, select, button, a, [contenteditable="true"]') !== null
  )
}


type Tab = 'general' | 'source' | 'candidates'

const TABS: Tab[] = ['general', 'source', 'candidates']


export function ItemScreen({ jobId, itemId }: { jobId: string; itemId: number }) {
  // The app-level message outlives this page: the undo of "accepted" stays after the walk on
  const { message: toast } = AntApp.useApp()
  const [item, setItem] = useState<ItemPage | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [missing, setMissing] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState<Tab>('general')
  const [editing, setEditing] = useState(false)

  const load = () =>
    api
      .item(itemId)
      .then((next) => {
        if(next.job_id !== jobId) throw new ApiError(T.item.missing, 404)
        setItem(next)
      })
      .catch((e) => {
        // the service words a missing position its own way; the page says it in its own words
        if(e instanceof ApiError && e.status === 404) setMissing(T.item.missing)
        else setMissing(e instanceof ApiError ? e.message : T.errors.network)
      })

  useEffect(() => {
    rememberItem(jobId, itemId)
    void load()
    api.job(jobId).then(setJob).catch(() => undefined)
    // the page is keyed by the item, so this runs once per position
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /** Runs a change of the item; false when it failed (the error is shown as a message). */
  const run = async (action: () => Promise<unknown>, done?: string) => {
    setBusy(true)
    try {
      await action()
      await load()
      if(done) toast.success(done)
      return true
    } catch(e) {
      toast.error(e instanceof ApiError ? e.message : T.errors.network)
      return false
    } finally {
      setBusy(false)
    }
  }

  const choose = (code: string) => run(() => api.correct(itemId, code), T.drawer.saved)
  const saveAttributes = async (attributes: AttributesInput) => {
    // On an error the form stays open with what the person typed
    if(await run(() => api.editAttributes(itemId, attributes), T.drawer.edited)) setEditing(false)
  }

  const revert = (done = T.item.reverted) => run(() => api.revert(itemId), done)

  /** Review follows doubtful rows, wrapping to the beginning when needed. */
  const nextTarget = item?.next_unreviewed_id ?? null

  /** Accept, then walk on to the next position that still needs a look. */
  const accept = async (goNext: boolean) => {
    if(!item || busy) return
    if(item.status !== 'accepted') {
      if(!item.ktru_code) return
      setBusy(true)
      try {
        await api.accept(itemId)
      } catch(e) {
        toast.error(e instanceof ApiError ? e.message : T.errors.network)
        setBusy(false)
        return
      }
      setBusy(false)
      toast.open({
        type: 'success',
        duration: 6,
        content: (
          <span>
            {T.item.acceptedDone}{' '}
            <button
              type="button"
              className="toast-undo"
              onClick={() =>
                void api
                  .revert(itemId)
                  .then(() => {
                    toast.info(T.item.undone)
                    void load()
                  })
                  .catch((e) => toast.error(e instanceof ApiError ? e.message : T.errors.network))
              }
            >
              <Undo2 size={14} aria-hidden="true" /> {T.item.undo}
            </button>
          </span>
        ),
      })
    }
    if(!goNext) return void load()
    if(item.next_unreviewed_id) navigate(paths.item(jobId, item.next_unreviewed_id))
    else {
      toast.success(T.results.reviewDone)
      void load()
    }
  }

  // Review mode: ↑ ↓ (or K J) move between positions, Enter accepts and opens the next one
  useEffect(() => {
    if(!item) return
    const onKey = (e: globalThis.KeyboardEvent) => {
      if(e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target)) return
      if(document.querySelector('.ant-modal-root')) return // a window is open: keys belong to it
      if(e.key === 'ArrowDown' || e.code === 'KeyJ') {
        if(item.next_id) {
          e.preventDefault()
          navigate(paths.item(jobId, item.next_id))
        }
      } else if(e.key === 'ArrowUp' || e.code === 'KeyK') {
        if(item.prev_id) {
          e.preventDefault()
          navigate(paths.item(jobId, item.prev_id))
        }
      } else if(e.key === 'Enter') {
        e.preventDefault()
        void accept(true)
      } else if(e.key === 'Escape') {
        navigate(paths.job(jobId)) // back to the list: the focus returns to this row
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const back = (
    <nav className="crumbs" aria-label={T.item.backToList}>
      <ArrowLeft size={16} aria-hidden="true" />
      <Link to={paths.job(jobId)}>{T.item.backToList}</Link>
      {job && (
        <>
          <ChevronRight size={14} className="crumb-sep" aria-hidden="true" />
          <Link className="crumb-now" to={paths.job(jobId)} title={job.filename}>
            {job.filename}
          </Link>
        </>
      )}
      {item && (
        <>
          <ChevronRight size={14} className="crumb-sep" aria-hidden="true" />
          <span className="crumb-now">{T.item.crumb(item.row_number)}</span>
        </>
      )}
    </nav>
  )

  if(missing) {
    return (
      <section className="wrap section">
        {back}
        <EmptyState icon={ScanSearch} title={missing} hint={T.job.missingHint}>
          <Link className="btn primary" to={paths.job(jobId)}>
            {T.item.backToList}
          </Link>
        </EmptyState>
      </section>
    )
  }
  if(!item) {
    return (
      <section className="wrap section" aria-busy="true">
        {back}
        <Skeleton height={260} />
      </section>
    )
  }

  const accepted = item.status === 'accepted'
  const reviewed = accepted || item.status === 'corrected'
  const previous = item.attributes.history?.at(-1) // the code before the last manual change
  const url = item.ktru_code ? ktruUrl(item.ktru_code) : null
  return (
    <>
      <section className="wrap item-page">
        {back}
        <div className="item-top">
          <div className="card item-head">
            <div className="item-code-row">
              <h1 className="item-code">{item.ktru_code ?? T.results.noCode}</h1>
              <StatusChip status={item.status} />
            </div>
            {item.ktru_name && <div className="item-name sentence">{item.ktru_name}</div>}
            {item.status === 'needs_review' && item.attributes.review && (
              <p className="review-reason">
                <b>{T.item.reviewWhy}</b> {item.attributes.review}
              </p>
            )}
            <RawText text={item.raw_text} />
            <div className="actions">
              {accepted ? (
                <button
                  type="button"
                  className="btn primary"
                  disabled={busy || !nextTarget}
                  title={nextTarget ? undefined : T.item.lastOne}
                  onClick={() => nextTarget && navigate(paths.item(jobId, nextTarget))}
                >
                  <ArrowRight size={16} aria-hidden="true" /> {T.item.nextItem}
                </button>
              ) : (
                <button
                  type="button"
                  className="btn primary"
                  disabled={busy || !item.ktru_code}
                  onClick={() => void accept(true)}
                >
                  <CheckCheck size={16} aria-hidden="true" />{' '}
                  {item.next_unreviewed_id ? T.item.acceptNext : T.item.accept}
                </button>
              )}
              {previous !== undefined && (
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  title={previous.ktru_code ?? undefined}
                  onClick={() => void run(() => api.undoCode(itemId), T.item.codeBack)}
                >
                  <Undo2 size={16} aria-hidden="true" /> {T.item.undoCode}
                </button>
              )}
              {reviewed && item.same_pending > 0 && (
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  title={T.item.applySameHint}
                  onClick={() =>
                    void run(() => api.applySame(itemId), T.item.appliedSame(item.same_pending))
                  }
                >
                  <CopyCheck size={16} aria-hidden="true" /> {T.item.applySame(item.same_pending)}
                </button>
              )}
              {reviewed && (
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  title={T.item.revertHint}
                  onClick={() => void revert()}
                >
                  <Undo2 size={16} aria-hidden="true" /> {T.item.revert}
                </button>
              )}
              {url && (
                <a className="btn" href={url} target="_blank" rel="noopener noreferrer" title={T.results.openEis}>
                  <ExternalLink size={16} aria-hidden="true" /> {T.item.openEis}
                </a>
              )}
            </div>
            <p className="muted keys-hint">{T.item.keysHint}</p>
          </div>
          <aside className="card item-side">
            <h2 className="section-h">{T.item.title}</h2>
            <dl className="side-facts">
              <div>
                <dt>{T.results.row}</dt>
                <dd className="num">{item.row_number}</dd>
              </div>
              <div>
                <dt>{T.results.status}</dt>
                <dd>
                  <StatusChip status={item.status} />
                </dd>
              </div>
              <div>
                <dt>{T.results.confidence}</dt>
                <dd>
                  <Confidence value={item.confidence} zone={item.zone} />
                </dd>
              </div>
              <div>
                <dt>{T.item.placeLabel}</dt>
                <dd className="num">{T.item.position(item.position, item.total)}</dd>
              </div>
            </dl>
            <div className="walk">
              <button
                type="button"
                className="btn small"
                disabled={!item.prev_id}
                title={T.item.prevTitle}
                onClick={() => item.prev_id && navigate(paths.item(jobId, item.prev_id))}
              >
                <ArrowUp size={16} aria-hidden="true" /> {T.item.prev}
              </button>
              <button
                type="button"
                className="btn small"
                disabled={!item.next_id}
                title={item.next_id ? T.item.nextTitle : T.item.lastOne}
                onClick={() => item.next_id && navigate(paths.item(jobId, item.next_id))}
              >
                <ArrowDown size={16} aria-hidden="true" /> {T.item.next}
              </button>
            </div>
          </aside>
        </div>
        <div className="tabs" role="tablist" aria-label={T.item.tabsLabel}>
          {TABS.map((key) => (
            <button
              key={key}
              type="button"
              role="tab"
              id={`tab-${key}`}
              aria-selected={tab === key}
              aria-controls="tab-body"
              className="tab"
              aria-label={T.item.tabs[key]}
              onClick={() => setTab(key)}
            >
              <span className="tab-long">{T.item.tabs[key]}</span>
              <span className="tab-short">{T.item.tabsShort[key]}</span>
            </button>
          ))}
        </div>
        <div className="tab-body stack" id="tab-body" role="tabpanel" aria-labelledby={`tab-${tab}`}>
          {tab === 'general' && <GeneralTab item={item} busy={busy} onChoose={choose} />}
          {tab === 'source' && (
            <SourceTab
              item={item}
              busy={busy}
              editing={editing}
              onEdit={setEditing}
              onSave={saveAttributes}
            />
          )}
          {tab === 'candidates' && <CandidatesTab item={item} onChoose={(code) => void choose(code)} />}
        </div>
      </section>
    </>
  )
}
