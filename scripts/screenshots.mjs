// Screenshots of the running site with headless Edge over the DevTools Protocol (Node 22+).
//
//   node scripts/screenshots.mjs <prefix> <done job id> [--site http://127.0.0.1:3000]
//
// Saves docs/screenshots/<prefix>-*.png: home with files, live processing, finished job, item
// panel, dark theme, phone 375 px. The live one uploads dataset/organizers/Спецификация_292 and
// stops that job right after the shot.
import { spawn } from 'node:child_process'
import { mkdirSync, mkdtempSync, writeFileSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const PORT = 9333
const [prefix = 'shot', doneJob] = process.argv.slice(2)
const siteArg = process.argv.indexOf('--site')
const SITE = siteArg > 0 ? process.argv[siteArg + 1] : 'http://127.0.0.1:3000'
const OUT = resolve('docs/screenshots')
const SPEC = resolve('dataset/organizers/Спецификация_292_без_КТРУ.xlsx')
const FILES = [SPEC, resolve('dataset/eis/ООЗ_мыло_крем_без_КТРУ.xlsx')]
const sleep = (ms) => new Promise((done) => setTimeout(done, ms))

const edge = spawn(EDGE, [
  '--headless=new',
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${mkdtempSync(join(tmpdir(), 'edge-shots-'))}`,
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
const run = (expression) => send('Runtime.evaluate', { expression, awaitPromise: true })

async function open(path, { width = 1440, height = 900, mobile = false, theme = 'light' } = {}) {
  await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile })
  await send('Page.navigate', { url: SITE })
  await sleep(500)
  await run(`localStorage.setItem('ktru-theme', '${theme}')`)
  await send('Page.navigate', { url: SITE + path })
  await sleep(2500)
}

async function shot(name, full = true) {
  const params = { format: 'png', captureBeyondViewport: full }
  if (full) {
    const { cssContentSize } = await send('Page.getLayoutMetrics')
    params.clip = { x: 0, y: 0, width: cssContentSize.width, height: cssContentSize.height, scale: 1 }
  }
  const { data } = await send('Page.captureScreenshot', params)
  writeFileSync(join(OUT, `${prefix}-${name}.png`), Buffer.from(data, 'base64'))
  console.log(`${prefix}-${name}.png`)
}

async function addFiles() {
  const { root } = await send('DOM.getDocument')
  const { nodeId } = await send('DOM.querySelector', { nodeId: root.nodeId, selector: 'input[type=file]' })
  await send('DOM.setFileInputFiles', { nodeId, files: FILES })
  await sleep(600)
}

async function upload() {
  const form = new FormData()
  form.append('file', new Blob([readFileSync(SPEC)]), 'Спецификация_292_без_КТРУ.xlsx')
  const response = await fetch(`${SITE}/api/jobs`, { method: 'POST', body: form })
  return (await response.json()).job_id
}

async function waitRows(jobId, rows) {
  for (let i = 0; i < 240; i++) {
    const job = await (await fetch(`${SITE}/api/jobs/${jobId}`)).json()
    if (job.processed >= rows || job.status !== 'processing') return
    await sleep(1000)
  }
}

mkdirSync(OUT, { recursive: true })
await send('Page.enable')
await send('DOM.enable')
try {
  await open('/')
  await addFiles()
  await shot('home')

  const live = await upload()
  await open(`/jobs/${live}`)
  await waitRows(live, 60)
  await sleep(1500)
  await shot('processing', false)
  await fetch(`${SITE}/api/jobs/${live}/cancel`, { method: 'POST' })

  if (doneJob) {
    await open(`/jobs/${doneJob}`)
    await shot('results')
    await run(`document.querySelector('.row-click')?.click()`)
    await sleep(1200)
    await shot('drawer', false)
    await open(`/jobs/${doneJob}`, { theme: 'dark' })
    await shot('dark')
    await open(`/jobs/${doneJob}`, { width: 375, height: 812, mobile: true })
    await shot('phone-results')
  }
  await open('/', { theme: 'dark' })
  await addFiles()
  await shot('dark-home')
  await open('/', { width: 375, height: 812, mobile: true })
  await addFiles()
  await shot('phone-home')
} finally {
  socket.close()
  edge.kill()
}
