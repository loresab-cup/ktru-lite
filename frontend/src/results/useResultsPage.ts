import { useEffect, useState } from 'react'
import { api, ApiError, type ItemRow, type Sort } from '../api'
import { T } from '../texts'

interface PageRequest {
  jobId: string
  processed: number
  status: string
  query: string
  sort: Sort | ''
  order: 'asc' | 'desc'
  page: number
  version: number
  pageSize: number
  setPage: (page: number) => void
  toast: { error: (message: string) => unknown }
}

/** Fetch a page and ignore responses belonging to an older request. */
export function useResultsPage({ jobId, processed, status, query, sort, order, page,
  version, pageSize, setPage, toast }: PageRequest) {
  const [data, setData] = useState<{ total: number; items: ItemRow[] } | null>(null)
  useEffect(() => {
    let alive = true
    api.items(jobId, { status, q: query, sort, order, limit: pageSize,
      offset: (page - 1) * pageSize })
      .then((next) => {
        if (!alive) return
        if (next.items.length === 0 && next.total > 0 && page > 1) {
          setPage(Math.ceil(next.total / pageSize))
        } else {
          setData(next)
        }
      })
      .catch((error) => alive && toast.error(error instanceof ApiError ? error.message : T.errors.network))
    return () => { alive = false }
  }, [jobId, processed, status, query, sort, order, page, version, pageSize, setPage, toast])
  return data
}
