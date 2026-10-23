import { App as AntApp, Input } from 'antd'
import {
  ArrowLeft,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Info,
  PenLine,
  Plus,
  Search,
  Trash2,
  Undo2,
} from 'lucide-react'
import { useEffect, useState } from 'react'
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
} from './api'
import { Link } from './Link'
import { navigate, paths } from './router'
import { T } from './texts'
import { Confidence, KtruCode, Skeleton, StatusChip, StatusMark } from './ui'

function CodeCard({ code, current, score, matched, onChoose }: {
  code: CatalogCode
  current: boolean
  score?: number
  matched?: string[]
  onChoose: () => void
}) {
  return (
    <div className={`cand ${current ? 'current' : ''}`}>
      <div className="cand-head">
        <div className="cand-name">
          <KtruCode code={code.code} />
          <div>{code.name}</div>
        </div>
        {code.status !== 'ACTIVE' && (
          <span className="chip low">{T.codeStatus[code.status] ?? code.status}</span>
        )}
      </div>
      {matched && matched.length > 0 && (
        <div className="tags" style={{ marginTop: 8 }}>
          {matched.map((m) => (
            <span key={m} className="chip high">
              {m}
            </span>
          ))}
        </div>
      )}
      <div className="cand-foot">
        {score !== undefined && (
          <>
            <span className="cell-sub num">
              {T.drawer.similarity} {score.toFixed(2)}
            </span>
            <div className="bar" aria-hidden="true">
              <span className="accent-fill" style={{ width: `${score * 100}%` }} />
            </div>
          </>
        )}
        {score === undefined && <span style={{ flex: 1 }} />}
        <button
          type="button"
          className={current ? 'btn' : 'btn primary'}
          onClick={onChoose}
          disabled={current}
          style={{ height: 34 }}
        >
          {current ? <Check size={15} aria-hidden="true" /> : null}
          {current ? T.drawer.chosen : T.drawer.choose}
        </button>
      </div>
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
    <div className="section">
      <div className="section-title">{T.drawer.search}</div>
      <Input
        allowClear
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        prefix={<Search size={15} aria-hidden="true" />}
        placeholder={T.drawer.searchPlaceholder}
        aria-label={T.drawer.search}
      />
      <div style={{ marginTop: 10 }} aria-live="polite">
        {loading && <Skeleton height={60} />}
        {!loading && visible?.length === 0 && <div className="cell-sub">{T.drawer.searchEmpty}</div>}
        {!loading &&
          visible?.map((code) => (
            <CodeCard
              key={code.code}
              code={code}
              current={code.code === item.ktru_code}
              onChoose={() => onChoose(code.code)}
            />
          ))}
      </div>
    </div>
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
        <dd>{attrs.name || T.drawer.none}</dd>
        <dt>{T.drawer.brand}</dt>
        <dd>{attrs.brand || T.drawer.none}</dd>
        <dt>{T.drawer.model}</dt>
        <dd>{attrs.model || T.drawer.none}</dd>
        <dt>{T.drawer.characteristics}</dt>
        <dd className="tags">
          {attrs.characteristics?.length
            ? attrs.characteristics.map((c, i) => (
                <span key={i} className="chip">
                  {characteristicText(c)}
                </span>
              ))
            : T.drawer.none}
        </dd>
      </dl>
      {attrs.source === 'rules' && (
        <div className="cell-sub" style={{ marginTop: 8 }}>
          <Info size={13} aria-hidden="true" style={{ verticalAlign: -2 }} />{' '}
          {T.drawer.fallback[attrs.fallback ?? ''] ?? T.drawer.byRules}
        </div>
      )}
      {attrs.source === 'llm' && attrs.provider && (
        <div className="cell-sub" style={{ marginTop: 8 }}>
          <Info size={13} aria-hidden="true" style={{ verticalAlign: -2 }} />{' '}
          {T.drawer.byProvider(attrs.provider)}
        </div>
      )}
      {attrs.source === 'manual' && (
        <div className="cell-sub" style={{ marginTop: 8 }}>
          <PenLine size={13} aria-hidden="true" style={{ verticalAlign: -2 }} /> {T.drawer.byHand}
        </div>
      )}
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
        {error && <span role="alert">{T.drawer.nameRequired}</span>}
      </label>
      <label>
        {T.drawer.brand}
        <Input value={brand} maxLength={100} onChange={(e) => setBrand(e.target.value)} />
      </label>
      <label>
        {T.drawer.model}
        <Input value={model} maxLength={100} onChange={(e) => setModel(e.target.value)} />
      </label>
      <div className="section-title">{T.drawer.characteristics}</div>
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
            placeholder={T.drawer.charUnit}
            aria-label={T.drawer.charUnit}
            onChange={(e) => setChar(i, 'unit', e.target.value)}
          />
          <button
            type="button"
            className="round ghost small"
            aria-label={T.drawer.removeChar}
            title={T.drawer.removeChar}
            onClick={() => setChars(chars.filter((_, j) => j !== i))}
          >
            <Trash2 size={15} aria-hidden="true" />
          </button>
        </div>
      ))}
      {chars.length < 30 && (
        <button
          type="button"
          className="btn small"
          onClick={() => setChars([...chars, { name: '', value: '', unit: '' }])}
        >
          <Plus size={15} aria-hidden="true" /> {T.drawer.addChar}
        </button>
      )}
      <div className="cell-sub">{T.drawer.editHint}</div>
      <div className="edit-actions">
        <button type="submit" className="btn primary" disabled={busy}>
          <Search size={15} aria-hidden="true" /> {T.drawer.save}
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          {T.drawer.cancel}
        </button>
      </div>
    </form>
  )
}

/** The current code and its manual input: any code of the catalog, typed or pasted. */
function CodeEditor({ item, busy, onSave }: {
  item: Item
  busy: boolean
  onSave: (code: string) => Promise<boolean>
}) {
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState('')
  const [error, setError] = useState(false)

  const save = async () => {
    const code = value.replace(/\s+/g, '') // "10.12.10.000- 00000005" from a spreadsheet
    if (!ktruUrl(code)) return setError(true)
    if (await onSave(code)) {
      setOpen(false)
      setValue('')
    }
  }

  return (
    <div className="code-edit">
      <div className="code-edit-row">
        {item.ktru_code ? (
          <KtruCode code={item.ktru_code} />
        ) : (
          <span className="muted">{T.drawer.noCode}</span>
        )}
        {!open && (
          <button type="button" className="btn small" onClick={() => setOpen(true)}>
            <PenLine size={15} aria-hidden="true" /> {T.drawer.enterCode}
          </button>
        )}
      </div>
      {open && (
        <form
          className="edit-form"
          style={{ marginTop: 12 }}
          onSubmit={(e) => {
            e.preventDefault()
            void save()
          }}
        >
          <label>
            {T.drawer.enterCode}
            <Input
              autoFocus
              value={value}
              maxLength={40}
              placeholder={T.drawer.codePlaceholder}
              status={error ? 'error' : undefined}
              aria-invalid={error}
              onChange={(e) => {
                setValue(e.target.value)
                setError(false)
              }}
            />
            {error && <span role="alert">{T.drawer.codeFormat}</span>}
          </label>
          <div className="edit-actions">
            <button type="submit" className="btn primary" disabled={busy || !value.trim()}>
              <Check size={15} aria-hidden="true" /> {T.drawer.saveCode}
            </button>
            <button type="button" className="btn" onClick={() => setOpen(false)}>
              {T.drawer.cancel}
            </button>
          </div>
        </form>
      )}
    </div>
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

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
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
  return (
    <>
      <h2 className="tab-h">{T.item.general}</h2>
      <dl className="facts-list">
        <Fact label={T.item.codeLabel}>
          <CodeEditor item={item} busy={busy} onSave={onChoose} />
        </Fact>
        <Fact label={T.item.catalogName}>{info?.name ?? item.ktru_name ?? T.item.none}</Fact>
        <Fact label={T.item.okpd2}>{info?.okpd2_code || T.item.none}</Fact>
        <Fact label={T.item.codeState}>
          {info ? (T.codeStatus[info.status] ?? info.status) : T.item.none}
        </Fact>
        <Fact label={T.results.confidence}>
          <div className="fact-conf">
            <Confidence value={item.confidence} zone={item.zone} />
          </div>
        </Fact>
        {item.summary && <Fact label={T.item.why}>{item.summary}</Fact>}
      </dl>
      {info && info.characteristics.length > 0 && (
        <>
          <h2 className="tab-h">{T.item.catalogChars}</h2>
          <dl className="facts-list">
            {info.characteristics.map(([name, values]) => (
              <Fact key={name} label={name}>
                {values.join('; ')}
              </Fact>
            ))}
          </dl>
        </>
      )}
      {info === null && item.ktru_code && <p className="muted">{T.item.noCatalog}</p>}
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
      <h2 className="tab-h">{T.drawer.source}</h2>
      <div className="raw">{item.raw_text}</div>
      <div className="section-head">
        <h2 className="tab-h">{T.drawer.attributes}</h2>
        {!editing && (
          <button type="button" className="btn small" onClick={() => onEdit(true)}>
            <PenLine size={15} aria-hidden="true" /> {T.drawer.edit}
          </button>
        )}
      </div>
      {editing ? (
        <AttributesForm item={item} busy={busy} onSave={onSave} onCancel={() => onEdit(false)} />
      ) : (
        <AttributesView item={item} />
      )}
    </>
  )
}

function CandidatesTab({ item, onChoose }: { item: ItemPage; onChoose: (code: string) => void }) {
  return (
    <>
      <h2 className="tab-h">{T.drawer.candidates}</h2>
      {item.candidates.map((c) => (
        <CodeCard
          key={c.code}
          code={c}
          score={c.score}
          matched={c.matched_characteristics}
          current={c.code === item.ktru_code}
          onChoose={() => onChoose(c.code)}
        />
      ))}
      <CatalogSearch key={`search-${item.id}`} item={item} onChoose={onChoose} />
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
        if (next.job_id !== jobId) throw new ApiError(T.app.notFound)
        setItem(next)
      })
      .catch((e) => setMissing(e instanceof ApiError ? e.message : T.errors.network))

  useEffect(() => {
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
          <span className="crumb-sep" aria-hidden="true">
            ›
          </span>
          <span className="crumb-now" title={job.filename}>
            {job.filename}
          </span>
        </>
      )}
      {item && (
        <>
          <span className="crumb-sep" aria-hidden="true">
            ›
          </span>
          <span className="crumb-now">{T.item.crumb(item.row_number)}</span>
        </>
      )}
    </nav>
  )

  if (missing) {
    return (
      <section className="band">
        <div className="wrap">
          {back}
          <h1 className="page-title">{missing}</h1>
          <p className="lead">{T.app.notFoundHint}</p>
        </div>
      </section>
    )
  }
  if (!item) {
    return (
      <section className="band" aria-busy="true">
        <div className="wrap">
          {back}
          <Skeleton height={190} />
        </div>
      </section>
    )
  }

  const reviewed = item.status === 'accepted' || item.status === 'corrected'
  const url = item.ktru_code ? ktruUrl(item.ktru_code) : null
  return (
    <>
      <section className="band item-band">
        <div className="wrap">
          {back}
          <div className="item-top">
            <div className="item-head">
              <div className="item-code-row">
                <h1 className="item-code">{item.ktru_code ?? T.results.noCode}</h1>
                <StatusMark status={item.status} size={28} />
              </div>
              {item.ktru_name && <div className="item-name">{item.ktru_name}</div>}
              <div className="res-meta">
                <span className="res-meta-label">{T.results.sourceText}:</span> {item.raw_text}
              </div>
              <div className="actions">
                <button
                  type="button"
                  className="btn primary"
                  disabled={busy || (!item.ktru_code && item.status !== 'accepted')}
                  onClick={() => void accept(true)}
                >
                  <CheckCheck size={16} aria-hidden="true" />{' '}
                  {item.next_unreviewed_id ? T.item.acceptNext : T.item.accept}
                </button>
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
                  <a className="btn" href={url} target="_blank" rel="noopener noreferrer">
                    <ExternalLink size={16} aria-hidden="true" /> {T.results.openEis}
                  </a>
                )}
              </div>
              <p className="muted keys-hint">{T.item.keysHint}</p>
            </div>
            <aside className="item-side">
              <h2 className="res-side-title">{T.item.title}</h2>
              <dl className="facts">
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
                  <ChevronUp size={16} aria-hidden="true" /> {T.item.prev}
                </button>
                <button
                  type="button"
                  className="btn small"
                  disabled={!item.next_id}
                  title={item.next_id ? T.item.nextTitle : T.item.lastOne}
                  onClick={() => item.next_id && navigate(paths.item(jobId, item.next_id))}
                >
                  <ChevronDown size={16} aria-hidden="true" /> {T.item.next}
                </button>
              </div>
            </aside>
          </div>
          <div className="tabs" role="tablist">
            {TABS.map((key) => (
              <button
                key={key}
                type="button"
                role="tab"
                id={`tab-${key}`}
                aria-selected={tab === key}
                aria-controls="tab-body"
                className="tab"
                onClick={() => setTab(key)}
              >
                {T.item.tabs[key]}
              </button>
            ))}
          </div>
        </div>
      </section>
      <div className="wrap section tab-body" id="tab-body" role="tabpanel" aria-labelledby={`tab-${tab}`}>
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
    </>
  )
}
