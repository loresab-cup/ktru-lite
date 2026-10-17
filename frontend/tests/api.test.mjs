import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

// Compile the actual module in memory; no browser, bundler or new dependency required.
const texts = 'export const T={errors:{network:"network",http:s=>`HTTP ${s}`}}'
const source = await readFile(new URL('../src/api.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText
const moduleText = compiled.replace("'./texts'", JSON.stringify(`data:text/javascript;base64,${Buffer.from(texts).toString('base64')}`))
const { api, ApiError } = await import(`data:text/javascript;base64,${Buffer.from(moduleText).toString('base64')}`)

test('API transport: success, safe HTTP errors, invalid JSON, network failure and timeout', async () => {
  const originalFetch = globalThis.fetch
  const originalTimeout = globalThis.setTimeout
  const originalClear = globalThis.clearTimeout
  try {
    globalThis.fetch = async () => new Response('{"catalog":"ready"}')
    assert.equal((await api.health()).catalog, 'ready')
    globalThis.fetch = async () => new Response('{"detail":"validation"}', { status: 422 })
    await assert.rejects(api.health(), { name: 'Error', message: 'validation' })
    globalThis.fetch = async () => new Response('<html>proxy</html>', { status: 502 })
    await assert.rejects(api.health(), { message: 'HTTP 502' })
    globalThis.fetch = async () => new Response('broken JSON')
    await assert.rejects(api.health(), e => e instanceof ApiError && e.message === 'network')
    globalThis.fetch = async () => { throw new TypeError('offline') }
    await assert.rejects(api.health(), { message: 'network' })
    let expire
    let cleared = false
    globalThis.setTimeout = callback => { expire = callback; return 123 }
    globalThis.clearTimeout = timer => { assert.equal(timer, 123); cleared = true }
    globalThis.fetch = async (_, { signal }) => new Promise((_, reject) => {
      signal.addEventListener('abort', () => reject(new DOMException('timeout', 'AbortError')))
    })
    const pending = api.health()
    expire()
    await assert.rejects(pending, { message: 'network' })
    assert.equal(cleared, true)
  } finally {
    globalThis.fetch = originalFetch
    globalThis.setTimeout = originalTimeout
    globalThis.clearTimeout = originalClear
  }
})
