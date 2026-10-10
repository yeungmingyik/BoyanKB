import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'

const source = readFileSync(new URL('./SkillSettings.vue', import.meta.url), 'utf8')
const script = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)![1]!
const ast = ts.createSourceFile('SkillSettings.ts', script, ts.ScriptTarget.Latest, true)
const functions = ['loadCatalog', 'load']
const code = ast.statements.filter((node) => ts.isFunctionDeclaration(node) && functions.includes(node.name!.text))
  .map((node) => ts.transpileModule(node.getText(ast), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText).join('\n')

function setup(nativeExecution: boolean, failCatalog = false, failSandbox = false) {
  const calls: string[] = []
  const context = {
    nativeExecution, loading: { value: false }, catalogError: { value: false }, sandboxError: { value: false },
    catalog: { value: [] }, records: { value: [] },
    listSkillCatalog: async () => { calls.push('catalog'); if (failCatalog) throw new Error('catalog_failed'); return { data: [{ id: 'catalog-one' }] } },
    listSandboxConfigs: async () => { calls.push('sandbox'); if (failSandbox) throw new Error('sandbox_failed'); return { data: [{ id: 'sandbox-one' }] } },
    emit: () => {}, ensurePoll: () => {}, t: (key: string) => key,
    MessagePlugin: { error: () => {} },
  }
  const load = runInNewContext(`${code}\nload`, context)
  return { context, calls, load }
}

test('catalog-only deployment does not call blocked sandbox APIs', async () => {
  const { load, context, calls } = setup(false)
  await load()
  assert.deepEqual(calls, ['catalog'])
  assert.equal(context.catalog.value.length, 1)
  assert.equal(context.catalogError.value, false)
  assert.equal(context.loading.value, false)
})

test('failed sandbox inspection keeps catalog available', async () => {
  const { load, context } = setup(true, false, true)
  await load()
  assert.equal(context.catalog.value.length, 1)
  assert.equal(context.catalogError.value, false)
  assert.equal(context.sandboxError.value, true)
})

test('catalog failure is distinct from an empty catalog', async () => {
  const { load, context } = setup(false, true)
  await load()
  assert.equal(context.catalogError.value, true)
  assert.equal(context.loading.value, false)
  assert.match(source, /!catalogError && catalog.length === 0/)
})
