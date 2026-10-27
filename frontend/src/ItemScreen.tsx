import { App as AntApp, Input, Modal } from 'antd'
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Pencil,
  Plus,
  ScanSearch,
  Search,
  Undo2,
  X,
} from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import {
  api,
  ApiError,
  type AttributesInput,
  type CatalogCode,
  type Characteristic,
  type Item,
  type ItemPage,
  type Job,
  ktruUrl,
  zoneOf,
} from './api'
import { Link } from './Link'
import { navigate, paths, rememberItem } from './router'
import { capital, T } from './texts'
import { Confidence, EmptyState, KtruCode, RawText, Skeleton, StatusChip } from './ui'

/** A code of the catalog or a candidate of the search: code, name, what matched, the button. */
function CodeCard({ code, current, confidence, matched, differs, onChoose }: {
  code: CatalogCode
  current: boolean
  confidence?: number
  matched?: string[]
  differs?: [string, string][]
  onChoose: () => void
}) {
  return (
    <div className={`cand ${current ? 'current' : ''}`}>
      <div className="cand-main">
        <div className="cand-title">
          <KtruCode code={code.code} />
          <span className="cand-name sentence">{code.name}</span>
          {code.status !== 'ACTIVE' && (
            <span className="status status-failed">
              <span className="status-dot" aria-hidden="true" />
              {capital(T.codeStatus[code.status] ?? code.status)}
            </span>
          )}
        </div>
        {confidence !== undefined && (
          <div className="cand-conf">
            {T.drawer.candidateConfidence}
            <span className={`num zone-${zoneOf(confidence)}`}>{Math.floor(confidence)} %</span>
          </div>
        )}
        {matched && matched.length > 0 && (
          <div className="cand-meta">
            {matched.map((m) => (
              <span key={m} className="chip good">
                <Check size={14} strokeWidth={2.5} aria-hidden="true" />
                {m}
              </span>
            ))}
          </div>
        )}
        {differs && differs.length > 0 && (
          <div className="cand-differs">
            <span className="muted">{T.drawer.differs}</span>
            {differs.map(([name, values]) => (
              <span key={name} className="chip">
                {name}: {values}
              </span>
            ))}
          </div>
        )}
      </div>
      <button
        type="button"
        className={`btn ${current ? 'soft' : ''}`}
        onClick={onChoose}
        disabled={current}
      >
        {current && <Check size={16} aria-hidden="true" />}
        {current ? T.drawer.chosen : T.drawer.choose}
      </button>
    </div>
  )
}

function CatalogSearch({ item, onChoose }: { item: Item; onChoose: (code: string) => void }) {
  const [query, setQuery] = useState('')
  const [found, setFound] = useState<CatalogCode[] | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    const q = query.trim()
    if (q.length < 2) return
    let alive = true
    const timer = window.setTimeout(() => {
      setLoading(true)
      api
        .catalog(q)
        .then((codes) => alive && setFound(codes))
        .catch(() => alive && setFound([]))
        .finally(() => alive && setLoading(false))
    }, 300)
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [query])

  const visible = query.trim().length >= 2 ? found : null
  return (
    <section className="card tab-card">
      <h2 className="tab-h">{T.drawer.search}</h2>
      <Input
        className="search-input"
        size="large"
        allowClear
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        prefix={<Search size={18} aria-hidden="true" />}
        placeholder={T.drawer.searchPlaceholder}
        aria-label={T.drawer.search}
      />
      <div aria-live="polite">
        {loading && <Skeleton height={72} />}
        {!loading && visible?.length === 0 && <p className="muted">{T.drawer.searchEmpty}</p>}
        {!loading && visible && visible.length > 0 && (
          <div className="cand-list">
            <b>{T.drawer.searchFound(visible.length)}</b>
            {visible.map((code) => (
              <CodeCard
                key={code.code}
                code={code}
                current={code.code === item.ktru_code}
                onChoose={() => onChoose(code.code)}
              />
            ))}
          </div>
        )}
      </div>
    </section>
  )
}

function characteristicText(c: Characteristic) {
  return [c.name, [c.value, c.unit].filter(Boolean).join(' ')].filter(Boolean).join(': ')
}

function AttributesView({ item }: { item: Item }) {
  const attrs = item.attributes
  return (
    <>
      <dl className="attrs">
        <dt>{T.drawer.name}</dt>
        <dd>{attrs.name || <span className="faint">{T.drawer.none}</span>}</dd>
        <dt>{T.drawer.brand}</dt>
        <dd>{attrs.brand || <span className="faint">{T.drawer.none}</span>}</dd>
        <dt>{T.drawer.model}</dt>
        <dd>{attrs.model || <span className="faint">{T.drawer.none}</span>}</dd>
        <dt>{T.drawer.characteristics}</dt>
        <dd className="chips">
          {attrs.characteristics?.length ? (
            attrs.characteristics.map((c, i) => (
              <span key={i} className="chip">
                {characteristicText(c)}
              </span>
            ))
          ) : (
            <span className="faint">{T.drawer.none}</span>
          )}
        </dd>
      </dl>
    </>
  )
}

/** Fix the attributes; on save the code is searched again by them (limits as in the API). */
function AttributesForm({ item, busy, onSave, onCancel }: {
  item: Item
  busy: boolean
  onSave: (attributes: AttributesInput) => void
  onCancel: () => void
}) {
  const attrs = item.attributes
  const [name, setName] = useState(attrs.name ?? '')
  const [brand, setBrand] = useState(attrs.brand ?? '')
  const [model, setModel] = useState(attrs.model ?? '')
  const [chars, setChars] = useState<Characteristic[]>(attrs.characteristics ?? [])
  const [error, setError] = useState(false)

  const setChar = (index: number, field: keyof Characteristic, value: string) =>
    setChars(chars.map((c, i) => (i === index ? { ...c, [field]: value } : c)))

  const save = () => {
    if (!name.trim()) return setError(true)
    const characteristics = chars
      .map((c) => ({ name: c.name.trim(), value: c.value.trim(), unit: c.unit.trim() }))
      .filter((c) => c.value) // a characteristic without a value says nothing
    onSave({ name: name.trim(), brand: brand.trim(), model: model.trim(), characteristics })
  }

  return (
    <form
      className="edit-form"
      onSubmit={(e) => {
        e.preventDefault()
        save()
      }}
    >
      <div className="edit-main">
        <label>
          {T.drawer.name}
          <Input
            value={name}
            maxLength={300}
            status={error ? 'error' : undefined}
            aria-invalid={error}
            onChange={(e) => {
              setName(e.target.value)
              setError(false)
            }}
          />
          {error && <span className="field-error" role="alert">{T.drawer.nameRequired}</span>}
        </label>
        <label>
          {T.drawer.brand}
          <Input value={brand} maxLength={100} placeholder={T.drawer.nameHint} onChange={(e) => setBrand(e.target.value)} />
        </label>
        <label>
          {T.drawer.model}
          <Input value={model} maxLength={100} placeholder={T.drawer.modelHint} onChange={(e) => setModel(e.target.value)} />
        </label>
      </div>
      <div className="edit-chars">
        <div className="field-title">{T.drawer.characteristics}</div>
        {chars.length > 0 && (
          <div className="char-row char-head" aria-hidden="true">
            <span>{T.drawer.charName}</span>
            <span>{T.drawer.charValue}</span>
            <span>{T.drawer.charUnit}</span>
            <span />
          </div>
        )}
        {chars.map((c, i) => (
          <div key={i} className="char-row">
            <Input
              value={c.name}
              maxLength={100}
              placeholder={T.drawer.charName}
              aria-label={T.drawer.charName}
              onChange={(e) => setChar(i, 'name', e.target.value)}
            />
            <Input
              value={c.value}
              maxLength={100}
              placeholder={T.drawer.charValue}
              aria-label={T.drawer.charValue}
              onChange={(e) => setChar(i, 'value', e.target.value)}
            />
            <Input
              value={c.unit}
              maxLength={30}
              placeholder={T.drawer.charUnitHint}
              aria-label={T.drawer.charUnit}
              onChange={(e) => setChar(i, 'unit', e.target.value)}
            />
            <button
              type="button"
              className="icon-btn"
              aria-label={T.drawer.removeChar}
              title={T.drawer.removeChar}
              onClick={() => setChars(chars.filter((_, j) => j !== i))}
            >
              <X size={18} aria-hidden="true" />
            </button>
          </div>
        ))}
        {chars.length < 30 && (
          <div>
            <button
              type="button"
              className="btn ghost small"
              onClick={() => setChars([...chars, { name: '', value: '', unit: '' }])}
            >
              <Plus size={16} aria-hidden="true" /> {T.drawer.addChar}
            </button>
          </div>
        )}
      </div>
      <div className="edit-actions">
        <button type="submit" className="btn primary" disabled={busy}>
          <Search size={16} aria-hidden="true" /> {T.drawer.save}
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          {T.drawer.cancel}
        </button>
        <span className="muted">{T.drawer.editHint}</span>
      </div>
    </form>
  )
}

type Known = { name: string; status: string } | 'unknown' | 'checking' | null

/** Manual input of a code in a window: the code is checked against the catalog as it is typed. */
function CodeDialog({ open, busy, onClose, onSave }: {
  open: boolean
  busy: boolean
  onClose: () => void
  onSave: (code: string) => Promise<boolean>
}) {
  const [value, setValue] = useState('')
  const [known, setKnown] = useState<Known>(null)
  const code = value.replace(/\s+/g, '') // "10.12.10.000- 00000005" from a spreadsheet
  const valid = ktruUrl(code) !== null

  useEffect(() => {
    if (!valid) return
    let alive = true
    const timer = window.setTimeout(() => {
      setKnown('checking')
      api
        .catalog(code)
        .then((list) => {
          const hit = list.find((c) => c.code === code)
          if (alive) setKnown(hit ? { name: hit.name, status: hit.status } : 'unknown')
        })
        .catch(() => alive && setKnown('unknown'))
    }, 300)
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [code, valid])

  const state = valid ? known : null
  const found = typeof state === 'object' && state !== null
  const submit = async () => {
    if (!found || busy) return
    if (await onSave(code)) {
      setValue('')
      onClose()
    }
  }

  let helper: ReactNode = <span className="muted">{T.drawer.codeFormat}</span>
  if (value && !valid) helper = <span className="field-error">{T.drawer.codeFormat}</span>
  else if (state === 'checking') helper = <span className="muted">{T.drawer.codeChecking}</span>
  else if (state === 'unknown') helper = <span className="field-error">{T.drawer.codeUnknown}</span>
  else if (found) {
    helper = (
      <span className="field-ok">
        {T.drawer.codeFound(state.name, T.codeStatus[state.status] ?? state.status)}
      </span>
    )
  }

  return (
    <Modal
      open={open}
      centered
      width={480}
      title={T.drawer.enterCodeTitle}
      okText={T.drawer.saveCode}
      cancelText={T.drawer.cancel}
      okButtonProps={{ disabled: !found || busy, icon: <Check size={16} /> }}
      onOk={() => void submit()}
      onCancel={onClose}
      destroyOnHidden
    >
      <p className="muted">{T.drawer.enterCodeHint}</p>
      <label className="modal-field">
        {T.drawer.codeLabel}
        <Input
          autoFocus
          value={value}
          maxLength={40}
          placeholder={T.drawer.codePlaceholder}
          status={value && (!valid || state === 'unknown') ? 'error' : undefined}
          onChange={(e) => setValue(e.target.value)}
          onPressEnter={() => void submit()}
        />
        <span aria-live="polite">{helper}</span>
      </label>
    </Modal>
  )
}

/** Keys of the review mode work everywhere on the page except fields, buttons and links. */
function isTyping(target: EventTarget | null) {
  return (
    target instanceof HTMLElement &&
    target.closest('input, textarea, select, button, a, [contenteditable="true"]') !== null
  )
}

type Tab = 'general' | 'source' | 'candidates'
const TABS: Tab[] = ['general', 'source', 'candidates']

/** What the catalog says about the code: from the candidates, else asked from the service. */
function useCatalogInfo(item: ItemPage): CatalogCode | null | undefined {
  const code = item.ktru_code
  const known = item.candidates.find((c) => c.code === code)
  const [fetched, setFetched] = useState<{ code: string; info: CatalogCode | null } | null>(null)
  useEffect(() => {
    if (!code || known) return
    let alive = true
    api
      .catalog(code)
      .then((list) => alive && setFetched({ code, info: list.find((c) => c.code === code) ?? null }))
      .catch(() => alive && setFetched({ code, info: null }))
    return () => {
      alive = false
    }
  }, [code, known])
  if (!code) return null
  if (known) return known
  return fetched?.code === code ? fetched.info : undefined // undefined: still loading
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="fact">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  )
}

function GeneralTab({ item, busy, onChoose }: {
  item: ItemPage
  busy: boolean
  onChoose: (code: string) => Promise<boolean>
}) {
  const info = useCatalogInfo(item)
  const [dialog, setDialog] = useState(false)
  const [chars, setChars] = useState(false) // what the code allows is a long list: closed at first
  const active = info?.status === 'ACTIVE'
  return (
    <>
      <section className="card tab-card">
        <h2 className="tab-h">{T.item.general}</h2>
        <dl className="facts-grid">
          <Fact label={T.item.codeLabel}>
            <div className="code-edit">
              {item.ktru_code ? <KtruCode code={item.ktru_code} /> : <span className="muted">{T.drawer.noCode}</span>}
              <button type="button" className="btn small" onClick={() => setDialog(true)}>
                <Pencil size={16} aria-hidden="true" /> {T.drawer.enterCode}
              </button>
            </div>
          </Fact>
          <Fact label={T.results.confidence}>
            <span className="fact-conf">
              <Confidence value={item.confidence} zone={item.zone} />
            </span>
          </Fact>
          <Fact label={T.item.catalogName}>
            <span className="sentence">{info?.name ?? item.ktru_name ?? T.item.none}</span>
          </Fact>
          {item.summary && <Fact label={T.item.why}>{item.summary}</Fact>}
          <Fact label={T.item.okpd2}>{info?.okpd2_code || T.item.none}</Fact>
          <Fact label={T.item.codeState}>
            {info ? (
              <span className={`status ${active ? 'status-accepted' : 'status-failed'}`}>
                <span className="status-dot" aria-hidden="true" />
                {capital(T.codeStatus[info.status] ?? info.status)}
              </span>
            ) : (
              T.item.none
            )}
          </Fact>
        </dl>
        {info === null && item.ktru_code && <p className="muted">{T.item.noCatalog}</p>}
      </section>
      {info && info.characteristics.length > 0 && (
        <section className="card tab-card fold">
          <button
            type="button"
            className="fold-head"
            aria-expanded={chars}
            onClick={() => setChars(!chars)}
          >
            {T.item.catalogChars(info.characteristics.length)}
            <ChevronDown size={18} aria-hidden="true" className={chars ? 'flip' : ''} />
          </button>
          {chars && (
            <dl className="facts-grid thirds">
              {info.characteristics.map(([name, values]) => (
                <Fact key={name} label={name}>
                  {values.join('; ')}
                </Fact>
              ))}
            </dl>
          )}
        </section>
      )}
      <CodeDialog open={dialog} busy={busy} onClose={() => setDialog(false)} onSave={onChoose} />
    </>
  )
}

function SourceTab({ item, busy, editing, onEdit, onSave }: {
  item: ItemPage
  busy: boolean
  editing: boolean
  onEdit: (on: boolean) => void
  onSave: (attributes: AttributesInput) => void
}) {
  return (
    <>
      <section className="card tab-card">
        <h2 className="tab-h">{T.drawer.source}</h2>
        <div className="raw">{item.raw_text}</div>
      </section>
      <section className="card tab-card">
        <div className="tab-head">
          <h2 className="tab-h">{editing ? T.drawer.editTitle : T.drawer.attributes}</h2>
          {!editing && (
            <button type="button" className="btn small" onClick={() => onEdit(true)}>
              <Pencil size={16} aria-hidden="true" /> {T.drawer.edit}
            </button>
          )}
        </div>
        {editing ? (
          <AttributesForm item={item} busy={busy} onSave={onSave} onCancel={() => onEdit(false)} />
        ) : (
          <AttributesView item={item} />
        )}
      </section>
    </>
  )
}

function CandidatesTab({ item, onChoose }: { item: ItemPage; onChoose: (code: string) => void }) {
  return (
    <>
      <CatalogSearch key={`search-${item.id}`} item={item} onChoose={onChoose} />
      <section className="card tab-card">
        <h2 className="tab-h">{T.drawer.candidates}</h2>
        <div className="cand-list">
          {item.candidates.map((c) => (
            <CodeCard
              key={c.code}
              code={c}
              confidence={c.confidence}
              matched={c.matched_characteristics}
              differs={c.differs}
              current={c.code === item.ktru_code}
              onChoose={() => onChoose(c.code)}
            />
          ))}
        </div>
      </section>
    </>
  )
}

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
        if (next.job_id !== jobId) throw new ApiError(T.item.missing, 404)
        setItem(next)
      })
      .catch((e) => {
        // the service words a missing position its own way; the page says it in its own words
        if (e instanceof ApiError && e.status === 404) setMissing(T.item.missing)
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
  const run = async (action: () => Promise<Item>, done?: string) => {
    setBusy(true)
    try {
      await action()
      await load()
      if (done) toast.success(done)
      return true
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : T.errors.network)
      return false
    } finally {
      setBusy(false)
    }
  }

  const choose = (code: string) => run(() => api.correct(itemId, code), T.drawer.saved)
  const saveAttributes = async (attributes: AttributesInput) => {
    // On an error the form stays open with what the person typed
    if (await run(() => api.editAttributes(itemId, attributes), T.drawer.edited)) setEditing(false)
  }

  const revert = (done = T.item.reverted) => run(() => api.revert(itemId), done)

  /** The next position to look at: the next one that still needs a look, else the next in order. */
  const nextTarget = item ? (item.next_unreviewed_id ?? item.next_id) : null

  /** Accept, then walk on to the next position that still needs a look. */
  const accept = async (goNext: boolean) => {
    if (!item || busy) return
    if (item.status !== 'accepted') {
      if (!item.ktru_code) return
      setBusy(true)
      try {
        await api.accept(itemId)
      } catch (e) {
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
    if (!goNext) return void load()
    if (item.next_unreviewed_id) navigate(paths.item(jobId, item.next_unreviewed_id))
    else {
      toast.success(T.results.reviewDone)
      void load()
    }
  }

  // Review mode: ↑ ↓ (or K J) move between positions, Enter accepts and opens the next one
  useEffect(() => {
    if (!item) return
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target)) return
      if (document.querySelector('.ant-modal-root')) return // a window is open: keys belong to it
      if (e.key === 'ArrowDown' || e.code === 'KeyJ') {
        if (item.next_id) {
          e.preventDefault()
          navigate(paths.item(jobId, item.next_id))
        }
      } else if (e.key === 'ArrowUp' || e.code === 'KeyK') {
        if (item.prev_id) {
          e.preventDefault()
          navigate(paths.item(jobId, item.prev_id))
        }
      } else if (e.key === 'Enter') {
        e.preventDefault()
        void accept(true)
      } else if (e.key === 'Escape') {
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

  if (missing) {
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
  if (!item) {
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
