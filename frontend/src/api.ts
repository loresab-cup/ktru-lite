import { T } from './texts'

export type JobStatus = 'queued' | 'processing' | 'done' | 'failed' | 'cancelled'
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
  fallback?: number // rows whose attributes came from the rules
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
    fallback?: string // why the rules were used
    provider?: string // which neural network extracted the row
  }
  candidates: Candidate[]
  ktru_code: string | null
  ktru_name: string | null
  confidence: number | null
  zone: Zone
  status: ItemStatus
  summary: string
}

/** One position with its place in the job: the page of a code walks to the neighbours. */
export interface ItemPage extends Item {
  job_id: string
  position: number
  total: number
  prev_id: number | null
  next_id: number | null
  next_unreviewed_id: number | null
}

export interface Health {
  catalog: 'ready' | 'loading' | 'error'
  codes: number
  llm: 'on' | 'off' | 'down'
}

export interface AttributesInput {
  name: string
  brand: string
  model: string
  characteristics: Characteristic[]
}

export class ApiError extends Error {
  status: number // HTTP status; 0 - no answer at all

  constructor(message: string, status = 0) {
    super(message)
    this.status = status
  }
}

/** Share of positions whose code needs no more work: auto, corrected and accepted. */
export function readyPercent(job: Job): number {
  const c = job.counts
  if (!c || !job.total) return 0
  return Math.round((100 * (c.auto + c.corrected + c.accepted)) / job.total)
}

const KTRU_CODE = /^\d{2}\.\d{2}\.\d{2}\.\d{3}-\d{5,8}$/

/** Search page of the KTRU registry in the official procurement system (EIS) for the code. */
export function ktruUrl(code: string): string | null {
  if (!KTRU_CODE.test(code)) return null // only real-looking codes become links
  const query = new URLSearchParams({ searchString: code, morphology: 'on' })
  return `https://zakupki.gov.ru/epz/ktru/search/results.html?${query}`
}

/** One request with a time limit; every failure becomes an ApiError. */
async function send(url: string, init?: RequestInit): Promise<Response> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 150_000)
  try {
    const response = await fetch(url, { ...init, signal: controller.signal })
    if (!response.ok) {
      let detail = ''
      try {
        detail = (await response.json()).detail
      } catch {
        // not JSON: nginx error page
      }
      throw new ApiError(
        typeof detail === 'string' && detail ? detail : T.errors.http(response.status),
        response.status,
      )
    }
    return response
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw new ApiError(T.errors.network)
  } finally {
    clearTimeout(timer)
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await send(url, init)
  if (response.status === 204) return undefined as T
  try {
    return (await response.json()) as T
  } catch {
    throw new ApiError(T.errors.http(response.status), response.status)
  }
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
})

export const api = {
  health: () => request<Health>('/api/health'),
  /** The newest jobs and how many there are in all. */
  jobs: async (limit = 8) => {
    const response = await send(`/api/jobs?limit=${limit}`)
    const jobs = (await response.json()) as Job[]
    return { jobs, total: Number(response.headers.get('X-Total-Count') ?? jobs.length) }
  },
  job: (id: string) => request<Job>(`/api/jobs/${encodeURIComponent(id)}`),
  deleteJob: (id: string) =>
    request<void>(`/api/jobs/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  item: (id: number) => request<ItemPage>(`/api/items/${id}`),
  revert: (itemId: number) => request<Item>(`/api/items/${itemId}/revert`, json('POST')),
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
  cancel: (id: string) => request<Job>(`/api/jobs/${encodeURIComponent(id)}/cancel`, json('POST')),
  correct: (itemId: number, code: string) =>
    request<Item>(`/api/items/${itemId}`, json('PATCH', { ktru_code: code })),
  editAttributes: (itemId: number, attributes: AttributesInput) =>
    request<Item>(`/api/items/${itemId}/attributes`, json('PUT', attributes)),
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
