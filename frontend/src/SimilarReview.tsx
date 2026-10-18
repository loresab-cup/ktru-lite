import { message } from 'antd'
import { useEffect, useState } from 'react'
import { api, ApiError, type Item, type SimilarItems } from './api'
import { T } from './texts'

export function SimilarReview({ item, onChanged }: { item: Item; onChanged: (item: Item) => void }) {
  const [group, setGroup] = useState<SimilarItems | null>(null)
  const [checked, setChecked] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [toast, holder] = message.useMessage()

  useEffect(() => {
    let alive = true
    api.similar(item.id).then((data) => {
      if (alive) { setGroup(data); setChecked(false); setError('') }
    }).catch((e) => {
      if (alive) { setGroup(null); setError(e instanceof ApiError ? e.message : T.errors.network) }
    })
    return () => { alive = false }
  }, [item.id, item.ktru_code, item.status])

  const apply = async () => {
    if (!group || !item.ktru_code || busy) return
    setBusy(true)
    try {
      const result = await api.applySimilar(item.id, item.ktru_code, group.token)
      setGroup(null)
      setChecked(false)
      onChanged(result.item)
      toast.success(T.drawer.groupSaved(result.updated))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : T.errors.network)
      setChecked(false)
    } finally { setBusy(false) }
  }

  if (!error && (!group || group.items.length < 2)) return null
  return <div className="section">
    {holder}
    <div className="section-title">{T.drawer.groupTitle}</div>
    {error && <div className="alert warn" role="alert">{error}</div>}
    {group && group.items.length >= 2 && <>
      <p className="cell-sub">{T.drawer.groupHint}</p>
      <details>
        <summary>{T.drawer.groupRows(group.items.length)}</summary>
        <ol className="group-rows">
          {group.items.map((row) => <li key={row.id}>
            <strong>{T.drawer.groupRow(row.row_number)}</strong> {row.raw_text}
          </li>)}
        </ol>
      </details>
      <label className="group-check">
        <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
        {T.drawer.groupChecked}
      </label>
      <button type="button" className="btn primary" disabled={!checked || busy || !item.ktru_code}
        onClick={() => void apply()}>{T.drawer.groupApply(group.items.length)}</button>
    </>}
  </div>
}
