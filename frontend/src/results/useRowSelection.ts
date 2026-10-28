import { useState } from 'react'
import type { ItemRow } from '../api'

const returnable = (item: ItemRow) => item.status === 'accepted' || item.status === 'corrected'

/** Keep selection independent from filtering and rendering the result cards. */
export function useRowSelection(items: ItemRow[]) {
  const [picking, setPicking] = useState(false)
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const pageRows = items.filter(returnable)
  const pageAllPicked = pageRows.length > 0 && pageRows.every((item) => picked.has(item.id))
  const pick = (id: number) => setPicked((previous) => {
    const next = new Set(previous)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })
  const stopPicking = () => {
    setPicking(false)
    setPicked(new Set())
  }
  return { picking, setPicking, picked, setPicked, pageRows, pageAllPicked, pick,
    stopPicking, returnable }
}
