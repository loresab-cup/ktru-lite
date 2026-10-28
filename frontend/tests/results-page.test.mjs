import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

test('result pages ignore stale responses, correct empty pages and report failures', async () => {
  const source = await readFile(new URL('../src/results/useResultsPage.ts', import.meta.url), 'utf8')
  const compiled = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
  } }).outputText
  const requests = [], errors = [], pages = []
  let data, cleanup
  const dependencies = {
    react: { useState: () => [null, (value) => { data = value }],
      useEffect: (effect) => { cleanup = effect() } },
    '../api': { api: { items: (...args) => new Promise((resolve, reject) => {
      requests.push({ args, resolve, reject })
    }) }, ApiError: class extends Error {} },
    '../texts': { T: { errors: { network: 'network' } } },
  }
  const exports = {}
  new Function('require', 'exports', compiled)((name) => dependencies[name], exports)
  const flush = () => new Promise((resolve) => setImmediate(resolve))
  const config = { jobId: 'job', processed: 0, status: '', query: '', sort: '', order: 'asc',
    page: 1, version: 0, pageSize: 20, setPage: (p) => pages.push(p),
    toast: { error: (error) => errors.push(error) } }
  exports.useResultsPage(config)
  cleanup()
  exports.useResultsPage({ ...config, query: 'new' })
  requests[1].resolve({ total: 1, items: [{ id: 2 }] })
  await flush()
  requests[0].resolve({ total: 1, items: [{ id: 1 }] })
  await flush()
  assert.deepEqual(data.items, [{ id: 2 }])
  assert.equal(requests[1].args[1].q, 'new')
  exports.useResultsPage({ ...config, page: 4 })
  requests[2].resolve({ total: 21, items: [] })
  await flush()
  assert.deepEqual(pages, [2])
  exports.useResultsPage(config)
  requests[3].reject(new Error('offline'))
  await flush()
  assert.deepEqual(errors, ['network'])
  exports.useResultsPage(config)
  cleanup()
  requests[4].reject(new Error('old request failed'))
  await flush()
  assert.deepEqual(errors, ['network'])
})
