import { T } from './texts'

export type JobStatus = 'queued' | 'processing' | 'done' | 'failed'
export type ItemStatus = 'auto' | 'needs_review' | 'corrected' | 'accepted' | 'failed'
export type Zone = 'high' | 'medium' | 'low'

export interface Job {
  id: string
  filename: string
  status: JobStatus
  total: number
  processed: number
  error: string | null
  include_inactive: boolean
  created_at: string
  stage_seconds: Record<string, number>
  counts?: Record<ItemStatus, number>
}

export interface CatalogCode {
  code: string
  name: string
  okpd2_code: string
  status: string
  characteristics: [string, string[]][]
}

export interface Candidate extends CatalogCode {
  score: number
  cosine: number
  shared_words: string[]
  matched_characteristics: string[]
}

export interface Characteristic {
  name: string
  value: string
  unit: string
}

export interface Item {
  id: number
  row_number: number
  raw_text: string
  attributes: {
    name?: string
    brand?: string
    model?: string
    characteristics?: Characteristic[]
    source?: string
  }
  candidates: Candidate[]
  ktru_code: string | null
  ktru_name: string | null
  confidence: number | null
  zone: Zone
  status: ItemStatus
  summary: string
}

export interface Health {
  catalog: 'ready' | 'loading' | 'error'
  codes: number
  llm: boolean
}

export class ApiError extends Error {}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(url, init)
  } catch {
    throw new ApiError(T.errors.network)
  }
  if (!response.ok) {
    let detail = ''
    try {
      detail = (await response.json()).detail
    } catch {
      // not JSON: nginx error page
    }
    throw new ApiError(typeof detail === 'string' && detail ? detail : T.errors.http(response.status))
  }
  return response.json() as Promise<T>
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
})

export const api = {
  health: () => request<Health>('/api/health'),
  jobs: () => request<Job[]>('/api/jobs?limit=8'),
  job: (id: string) => request<Job>(`/api/jobs/${encodeURIComponent(id)}`),
  items: (id: string, params: { status?: string; q?: string; limit: number; offset: number }) => {
    const query = new URLSearchParams({
      status: params.status ?? '',
      q: params.q ?? '',
      limit: String(params.limit),
      offset: String(params.offset),
    })
    return request<{ total: number; items: Item[] }>(
      `/api/jobs/${encodeURIComponent(id)}/items?${query}`,
    )
  },
  upload: (file: File, includeInactive: boolean) => {
    const form = new FormData()
    form.append('file', file)
    form.append('include_inactive', String(includeInactive))
    return request<{ job_id: string }>('/api/jobs', { method: 'POST', body: form })
  },
  correct: (itemId: number, code: string) =>
    request<Item>(`/api/items/${itemId}`, json('PATCH', { ktru_code: code })),
  accept: (itemId: number) => request<Item>(`/api/items/${itemId}/accept`, json('POST')),
  acceptAll: (jobId: string, minConfidence: number) =>
    request<{ accepted: number }>(
      `/api/jobs/${encodeURIComponent(jobId)}/accept?min_confidence=${minConfidence}`,
      json('POST'),
    ),
  catalog: (q: string) =>
    request<CatalogCode[]>(`/api/catalog/search?q=${encodeURIComponent(q)}`),
  exportUrl: (jobId: string, format: 'xlsx' | 'csv') =>
    `/api/jobs/${encodeURIComponent(jobId)}/export?format=${format}`,
}
