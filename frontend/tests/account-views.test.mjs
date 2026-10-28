import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/router.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
} }).outputText.replace(/from ['"]react['"]/, `from ${JSON.stringify(import.meta.resolve('react'))}`)
const router = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)

test('switching accounts clears private navigation state without clearing unrelated settings', () => {
  const original = globalThis.sessionStorage
  const storage = {}
  Object.defineProperties(storage, {
    setItem: { value: (key, value) => { storage[key] = value } },
    getItem: { value: (key) => storage[key] ?? null },
    removeItem: { value: (key) => { delete storage[key] } },
  })
  globalThis.sessionStorage = storage
  try {
    router.saveListView('private-job', { page: 4, search: 'private', filters: [], sort: '', order: 'asc' })
    router.rememberItem('private-job', 123)
    storage.setItem('ktru-jobs-view', 'private search')
    storage.setItem('unrelated', 'keep')
    router.clearAccountViews()
    assert.equal(router.savedListView('private-job'), undefined)
    assert.equal(router.lastItemOf('private-job'), null)
    assert.deepEqual(Object.keys(storage), ['unrelated'])
    assert.equal(router.parseRoute('/privacy').page, 'legal')
    assert.equal(router.parseRoute('/consent/files').document, 'files')
  } finally { globalThis.sessionStorage = original }
})
