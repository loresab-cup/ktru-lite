// Pictures of the tour from the running site, with headless Edge over the DevTools Protocol.
//
//   node scripts/tour-shots.mjs [--site http://127.0.0.1:5173]
//
// The site has accounts: TOUR_LOGIN and TOUR_PASSWORD in the environment (a test user of
// backend/users.py); the script logs in and gives the session to the browser.
// Uploads frontend/public/primer.xlsx, takes one shot per step with the place to press lit up,
// saves frontend/public/tour/<step>.webp and deletes the job it made.
import { spawn } from 'node:child_process'
import { mkdirSync, mkdtempSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const PORT = 9334
const siteArg = process.argv.indexOf('--site')
const SITE = siteArg > 0 ? process.argv[siteArg + 1] : 'http://127.0.0.1:5173'
const OUT = resolve('frontend/public/tour')
const EXAMPLE = resolve('frontend/public/primer.xlsx')
const VIEW = { width: 1280, height: 860 }
const CLIP = { width: 1120, height: 630 } // the picture: 16:9, as the tour shows it
const SCALE = 1.25 // pixels per CSS pixel: sharp in the tour window, still under 150 KB
const sleep = (ms) => new Promise((done) => setTimeout(done, ms))

const edge = spawn(EDGE, [
  '--headless=new',
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${mkdtempSync(join(tmpdir(), 'edge-tour-'))}`,
  '--hide-scrollbars',
  'about:blank',
])

async function connect() {
  for (let i = 0; i < 50; i++) {
    try {
      const pages = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()
      const page = pages.find((p) => p.type === 'page')
      if (page) return new WebSocket(page.webSocketDebuggerUrl)
    } catch {
      // Edge is still starting
    }
    await sleep(200)
  }
  throw new Error('Edge did not start')
}

const socket = await connect()
await new Promise((done) => socket.addEventListener('open', done, { once: true }))
let lastId = 0
const waiting = new Map()
socket.addEventListener('message', (event) => {
  const message = JSON.parse(event.data)
  if (message.id && waiting.has(message.id)) {
    waiting.get(message.id)(message)
    waiting.delete(message.id)
  }
})
function send(method, params = {}) {
  const id = ++lastId
  socket.send(JSON.stringify({ id, method, params }))
  return new Promise((done, fail) =>
    waiting.set(id, (m) => (m.error ? fail(new Error(`${method}: ${m.error.message}`)) : done(m.result))),
  )
}
async function run(expression) {
  const { result } = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
  return result.value
}

async function open(path) {
  await send('Page.navigate', { url: SITE + path })
  await sleep(2500)
}

/** Lights up the elements: a ring around each, the rest of the page a little darker when one. */
const LIGHT = `(selector, text) => {
  const all = [...document.querySelectorAll(selector)]
  const found = text ? all.filter((e) => e.textContent.includes(text)) : all
  if (!found.length) throw new Error('nothing for ' + selector + ' ' + (text || ''))
  for (const e of found) {
    e.style.outline = '3px solid #1f5fd6'
    e.style.outlineOffset = '4px'
    e.style.position = e.style.position || 'relative'
    e.style.zIndex = '20'
  }
  if (found.length === 1) found[0].style.boxShadow = '0 0 0 9999px rgba(16, 24, 40, 0.38)'
  found[0].scrollIntoView({ block: 'center' })
  const box = found.map((e) => e.getBoundingClientRect())
  const left = Math.min(...box.map((b) => b.left)), right = Math.max(...box.map((b) => b.right))
  const top = Math.min(...box.map((b) => b.top)), bottom = Math.max(...box.map((b) => b.bottom))
  return { x: (left + right) / 2 + scrollX, y: (top + bottom) / 2 + scrollY }
}`

/** fixed: the element stays in the window (the round buttons), so only the window is shot. */
async function shot(name, selector, text = '', fixed = false) {
  // the recent jobs of the home page are the team's data: not in the pictures
  await run(`document.querySelector('[aria-labelledby=recent-title]')?.remove()`)
  const center = await run(`(${LIGHT})(${JSON.stringify(selector)}, ${JSON.stringify(text)})`)
  await sleep(400)
  const { cssContentSize, cssVisualViewport: view } = await send('Page.getLayoutMetrics')
  let x = Math.max(0, (cssContentSize.width - CLIP.width) / 2) // the page column, whole
  let y = Math.max(0, Math.min(center.y - CLIP.height / 2, cssContentSize.height - CLIP.height))
  if (fixed) {
    x = view.pageX + view.clientWidth - CLIP.width
    y = view.pageY + view.clientHeight - CLIP.height
  }
  const { data } = await send('Page.captureScreenshot', {
    format: 'webp',
    quality: 80,
    captureBeyondViewport: !fixed,
    clip: { x, y, ...CLIP, scale: 1 },
  })
  const file = join(OUT, `${name}.webp`)
  writeFileSync(file, Buffer.from(data, 'base64'))
  console.log(`${name}.webp ${Math.round(statSync(file).size / 1024)} KB`)
}

const click = (selector, text) =>
  run(`[...document.querySelectorAll(${JSON.stringify(selector)})]
    .find((e) => e.textContent.includes(${JSON.stringify(text)})).click()`)

/** Logs in; the session cookie goes to every request of the script and to the browser. */
async function logIn() {
  const login = process.env.TOUR_LOGIN
  const password = process.env.TOUR_PASSWORD
  if (!login || !password) throw new Error('set TOUR_LOGIN and TOUR_PASSWORD')
  const answer = await fetch(`${SITE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ login, password }),
  })
  if (!answer.ok) throw new Error(`login: HTTP ${answer.status}`)
  const [name, value] = answer.headers.getSetCookie()[0].split(';')[0].split('=')
  await send('Network.setCookie', { name, value, url: SITE, httpOnly: true })
  return { cookie: `${name}=${value}` }
}

async function upload() {
  const form = new FormData()
  form.append('file', new Blob([readFileSync(EXAMPLE)]), 'Пример спецификации.xlsx')
  const job = await (await fetch(`${SITE}/api/jobs`, { method: 'POST', body: form, headers })).json()
  for (let i = 0; i < 240; i++) {
    const state = await (await fetch(`${SITE}/api/jobs/${job.job_id}`, { headers })).json()
    if (state.status !== 'queued' && state.status !== 'processing') return job.job_id
    await sleep(1000)
  }
  throw new Error('the example was not processed in 4 minutes')
}

mkdirSync(OUT, { recursive: true })
await send('Page.enable')
await send('Network.enable')
const headers = await logIn()
await send('Emulation.setDeviceMetricsOverride', { ...VIEW, deviceScaleFactor: SCALE, mobile: false })
await open('/')
await run(`localStorage.setItem('ktru-tour-seen', '1')`) // the tour must not cover the shots
const jobId = await upload()
try {
  await open('/')
  await shot('upload', '.drop')
  await open('/')
  await click('button', 'Взять пример')
  await sleep(800)
  await shot('process', '.file-actions .btn.primary')

  await open(`/jobs/${jobId}`)
  await shot('colors', '.res-conf, .res-status')
  await open(`/jobs/${jobId}`)
  await shot('review', '.review .btn.primary')
  await open(`/jobs/${jobId}`)
  await shot('export', '.dock .round.primary', '', true)

  const items = await (await fetch(`${SITE}/api/jobs/${jobId}/items?status=needs_review&limit=1`, { headers })).json()
  const itemId = (items.items[0] ?? (await (await fetch(`${SITE}/api/jobs/${jobId}/items?limit=1`, { headers })).json()).items[0]).id
  await open(`/jobs/${jobId}/items/${itemId}`)
  await shot('accept', '.item-head .btn.primary')
  await open(`/jobs/${jobId}/items/${itemId}`)
  await click('[role=tab]', 'Кандидаты')
  await sleep(800)
  await shot('replace', '.cand-list .cand:nth-child(2) .btn')

  await open('/catalog')
  await run(`(() => {
    const input = document.querySelector('.home-card input')
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
    set.call(input, 'бумага для офисной техники')
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })()`)
  await sleep(2500)
  await shot('catalog', '.main-nav a[href="/catalog"]')
} finally {
  await fetch(`${SITE}/api/jobs/${jobId}`, { method: 'DELETE', headers })
  socket.close()
  edge.kill()
}
