import { useSyncExternalStore } from 'react'

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

export const paths = {
  home: '/',
  catalog: '/catalog',
  jobs: '/jobs',
  job: (id: string) => `/jobs/${id}`,
  item: (jobId: string, itemId: number) => `/jobs/${jobId}/items/${itemId}`,
}

export type Route =
  | { page: 'home' }
  | { page: 'catalog' }
  | { page: 'jobs' }
  | { page: 'job'; jobId: string }
  | { page: 'item'; jobId: string; itemId: number }
  | { page: 'missing' }

export function parseRoute(path: string): Route {
  const clean = path.replace(/\/+$/, '') || '/'
  if (clean === '/') return { page: 'home' }
  if (clean === '/catalog') return { page: 'catalog' }
  if (clean === '/jobs') return { page: 'jobs' }
  const item = clean.match(/^\/jobs\/([\w-]+)\/items\/(\d+)$/)
  if (item) return { page: 'item', jobId: item[1], itemId: Number(item[2]) }
  const job = clean.match(/^\/jobs\/([\w-]+)$/)
  if (job) return { page: 'job', jobId: job[1] }
  return { page: 'missing' }
}
