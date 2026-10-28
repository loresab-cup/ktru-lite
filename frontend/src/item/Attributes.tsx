import { Input } from 'antd'
import {
  Pencil,
  Plus,
  Search,
  X
} from 'lucide-react'
import { useState } from 'react'
import {
  type AttributesInput,
  type Characteristic,
  type Item,
  type ItemPage
} from '../api'
import { T } from '../texts'


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
    if(!name.trim()) return setError(true)
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


export function SourceTab({ item, busy, editing, onEdit, onSave }: {
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
