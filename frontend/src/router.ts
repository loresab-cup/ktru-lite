import { useSyncExternalStore } from 'react'
import type { Sort } from './api'

// Five pages, plain history API (nginx serves index.html for every path):
//   /                          upload and recent specifications
//   /catalog                   search of the KTRU catalog (the other tab of the same page)
//   /jobs                      all specifications
//   /jobs/<job>                list of the codes of one specification
//   /jobs/<job>/items/<item>   page of one code

const CHANGED = 'ktru-navigate'

function subscribe(onChange: () => void) {
  window.addEventListener('popstate', onChange)
  window.addEventListener(CHANGED, onChange)
  return () => {
    window.removeEventListener('popstate', onChange)
    window.removeEventListener(CHANGED, onChange)
  }
}

export function usePath(): string {
  return useSyncExternalStore(subscribe, () => window.location.pathname)
}

export function navigate(path: string) {
  if (path === window.location.pathname) return
  window.history.pushState(null, '', path)
  window.dispatchEvent(new Event(CHANGED))
  window.scrollTo(0, 0)
}

/** How the person looked at the list of a job: back from a position it opens the same page. */
export interface ListView {
  page: number
  filters: string[]
  search: string
  sort: Sort | ''
  order: 'asc' | 'desc'
}

const listViews = new Map<string, ListView>()
const VIEW_KEY = 'ktru-list-view:' // + job id; the tab keeps it over a reload of the page

export function savedListView(jobId: string): ListView | undefined {
  if (listViews.has(jobId)) return listViews.get(jobId)
  try {
    const stored = sessionStorage.getItem(VIEW_KEY + jobId)
    return stored ? (JSON.parse(stored) as ListView) : undefined
  } catch {
    return undefined // no storage: the list opens at its first page
  }
}

export function saveListView(jobId: string, view: ListView) {
  listViews.set(jobId, view)
  try {
    sessionStorage.setItem(VIEW_KEY + jobId, JSON.stringify(view))
  } catch {
    // the view is still kept in memory for this visit
  }
}

/** The position the person looked at last: the list of its job takes the focus back to it. */
let lastItem: { jobId: string; itemId: number } | null = null

export function rememberItem(jobId: string, itemId: number) {
  lastItem = { jobId, itemId }
}

export function lastItemOf(jobId: string): number | null {
  return lastItem?.jobId === jobId ? lastItem.itemId : null
}

export function forgetItem() {
  lastItem = null
}

export function clearAccountViews() {
  listViews.clear()
  lastItem = null
  try {
    for (const key of Object.keys(sessionStorage)) {
      if (key === 'ktru-jobs-view' || key.startsWith(VIEW_KEY)) sessionStorage.removeItem(key)
    }
  } catch { /* Storage is optional. */ }
}

export const paths = {
  home: '/',
  catalog: '/catalog',
  jobs: '/jobs',
  profile: '/profile',
  privacy: '/privacy',
  cookies: '/cookies',
  consent: (purpose: string) => `/consent/${purpose}`,
  job: (id: string) => `/jobs/${id}`,
  item: (jobId: string, itemId: number) => `/jobs/${jobId}/items/${itemId}`,
}

export type Route =
  | { page: 'home' }
  | { page: 'catalog' }
  | { page: 'jobs' }
  | { page: 'profile' }
  | { page: 'legal'; document: 'privacy' | 'cookies' | 'account' | 'files' | 'improvement' }
  | { page: 'job'; jobId: string }
  | { page: 'item'; jobId: string; itemId: number }
  | { page: 'missing' }

export function parseRoute(path: string): Route {
  const clean = path.replace(/\/+$/, '') || '/'
  if (clean === '/') return { page: 'home' }
  if (clean === '/catalog') return { page: 'catalog' }
  if (clean === '/jobs') return { page: 'jobs' }
  if (clean === '/profile') return { page: 'profile' }
  if (clean === '/privacy') return { page: 'legal', document: 'privacy' }
  if (clean === '/cookies') return { page: 'legal', document: 'cookies' }
  const consent = clean.match(/^\/consent\/(account|files|improvement)$/)
  if (consent) return { page: 'legal', document: consent[1] as 'account' | 'files' | 'improvement' }
  const item = clean.match(/^\/jobs\/([\w-]+)\/items\/(\d+)$/)
  if (item) return { page: 'item', jobId: item[1], itemId: Number(item[2]) }
  const job = clean.match(/^\/jobs\/([\w-]+)$/)
  if (job) return { page: 'job', jobId: job[1] }
  return { page: 'missing' }
}
