import { Drawer, Input, message } from 'antd'
import { Check, CheckCheck, Info, PenLine, Plus, Search, Trash2, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import {
  api,
  ApiError,
  type AttributesInput,
  type CatalogCode,
  type Characteristic,
  type Item,
  ktruUrl,
} from './api'
import { T } from './texts'
import { Confidence, KtruCode, Skeleton, StatusChip } from './ui'

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
    <div className="section">
      <div className="section-head">
        <div className="section-title">{T.drawer.code}</div>
        {!open && (
          <button type="button" className="btn small" onClick={() => setOpen(true)}>
            <PenLine size={15} aria-hidden="true" /> {T.drawer.enterCode}
          </button>
        )}
      </div>
      {item.ktru_code ? (
        <div className="cand-name">
          <KtruCode code={item.ktru_code} />
          <div>{item.ktru_name}</div>
        </div>
      ) : (
        <div className="cell-sub">{T.drawer.noCode}</div>
      )}
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

export function ItemDrawer({ item, onClose, onChanged }: {
  item: Item | null
  onClose: () => void
  onChanged: (item: Item) => void
}) {
  const [toast, toastHolder] = message.useMessage()
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState<number | null>(null) // id of the edited item

  /** Runs a change of the item; false when it failed (the error is shown as a message). */
  const run = async (action: () => Promise<Item>, done: string) => {
    setBusy(true)
    try {
      onChanged(await action())
      toast.success(done)
      return true
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : T.errors.network)
      return false
    } finally {
      setBusy(false)
    }
  }

  const choose = (code: string) =>
    item ? run(() => api.correct(item.id, code), T.drawer.saved) : Promise.resolve(false)
  const saveAttributes = async (attributes: AttributesInput) => {
    if (!item) return
    // On an error the form stays open with what the person typed
    if (await run(() => api.editAttributes(item.id, attributes), T.drawer.edited)) setEditing(null)
  }
  const attrs = item?.attributes

  return (
    <Drawer
      open={item !== null}
      onClose={onClose}
      placement="right"
      size={520}
      destroyOnHidden
      title={item ? T.drawer.title(item.row_number) : ''}
      closeIcon={<X size={18} aria-label={T.drawer.close} />}
      extra={
        item && (
          <button
            type="button"
            className="btn primary"
            disabled={busy || !item.ktru_code || item.status === 'accepted'}
            onClick={() => run(() => api.accept(item.id), T.drawer.acceptedDone)}
          >
            <CheckCheck size={16} aria-hidden="true" /> {T.drawer.accept}
          </button>
        )
      }
    >
      {toastHolder}
      {item && attrs && (
        <>
          <div className="section" style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
            <Confidence value={item.confidence} zone={item.zone} />
            <StatusChip status={item.status} />
          </div>

          <CodeEditor key={item.id} item={item} busy={busy} onSave={choose} />

          <div className="section">
            <div className="section-title">{T.drawer.source}</div>
            <div className="raw">{item.raw_text}</div>
          </div>

          <div className="section">
            <div className="section-head">
              <div className="section-title">{T.drawer.attributes}</div>
              {editing !== item.id && (
                <button type="button" className="btn small" onClick={() => setEditing(item.id)}>
                  <PenLine size={15} aria-hidden="true" /> {T.drawer.edit}
                </button>
              )}
            </div>
            {editing === item.id ? (
              <AttributesForm
                item={item}
                busy={busy}
                onSave={saveAttributes}
                onCancel={() => setEditing(null)}
              />
            ) : (
              <AttributesView item={item} />
            )}
          </div>

          {item.summary && (
            <div className="section">
              <div className="section-title">{T.drawer.why}</div>
              <div className="summary">{item.summary}</div>
            </div>
          )}

          <div className="section">
            <div className="section-title">{T.drawer.candidates}</div>
            {item.candidates.map((c) => (
              <CodeCard
                key={c.code}
                code={c}
                score={c.score}
                matched={c.matched_characteristics}
                current={c.code === item.ktru_code}
                onChoose={() => choose(c.code)}
              />
            ))}
          </div>

          <CatalogSearch key={item.id} item={item} onChoose={choose} />
        </>
      )}
    </Drawer>
  )
}
