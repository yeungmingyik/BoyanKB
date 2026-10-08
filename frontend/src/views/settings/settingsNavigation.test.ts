import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'
import { computed, reactive, ref } from 'vue'
import { isProviderHidden } from '../../config/branding.ts'
import { SETTINGS_SECTION_MIN_ROLE } from '../../config/settingsAccess'
import { INTEGRATION_PREVIEW_ITEMS } from '../../config/integrations'
import { integrationSectionKey } from '../../config/settingsRoute'

// Exercise the actual sidebar grouping without mounting the settings panels.
const source = readFileSync(new URL('./Settings.vue', import.meta.url), 'utf8')
const script = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)![1]!
const ast = ts.createSourceFile('Settings.ts', script, ts.ScriptTarget.Latest, true)
const grouping = ast.statements.find((statement) =>
  ts.isVariableStatement(statement) && statement.declarationList.declarations.some(
    (declaration) => ts.isIdentifier(declaration.name) && declaration.name.text === 'navGroups',
  ),
)
assert.ok(grouping, 'Settings must define sidebar groups')
const compiled = ts.transpileModule(grouping.getText(ast), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText

function integrationMenu(visibleKeys: string[]): string[] {
  const groups = runInNewContext(`${compiled}\nnavGroups.value`, {
    computed,
    navItems: { value: visibleKeys.map((key) => ({ key })) },
    t: (key: string) => key,
    integrationSectionKey,
    INTEGRATION_PREVIEW_ITEMS,
  }) as Array<{ key: string; items: Array<{ key: string }> }>
  const integrationGroup = groups.find((group) => group.key === 'integrations')
  return Array.from(integrationGroup?.items ?? [], (item) => item.key)
}

const integrationKeys = INTEGRATION_PREVIEW_ITEMS.map((item) => integrationSectionKey(item.key))

test('settings sidebar includes every registered integration, including CLI, in navigation order', () => {
  assert.deepEqual(integrationMenu(['general', ...integrationKeys]), integrationKeys)
})

test('settings sidebar preserves visibility filtering without hiding CLI', () => {
  const visible = integrationKeys.filter((key) => key !== 'integration-api' && key !== 'integration-im')
  assert.deepEqual(integrationMenu(visible), visible)
  assert.deepEqual(integrationMenu(['general']), [])
})

const providerBindings = new Set([
  'wkcTenantKey', 'wkcAccess', 'wkcAccessRequest', 'hasExistingWkc',
  'isProviderSectionHidden', 'isSectionSupportPending', 'loadExistingWkcAccess', 'canSeeSection',
])
const providerCode = ts.transpileModule(ast.statements.filter((statement) => {
  if (ts.isFunctionDeclaration(statement)) return !!statement.name && providerBindings.has(statement.name.text)
  return ts.isVariableStatement(statement) && statement.declarationList.declarations.some(
    declaration => ts.isIdentifier(declaration.name) && providerBindings.has(declaration.name.text),
  )
}).map(statement => statement.getText(ast)).join('\n'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText

function providerAccessFixture(
  status: () => Promise<{ has_models: boolean; needs_reinit: boolean }>,
  models: () => Promise<Array<{ parameters: { provider: string } }>>,
) {
  const authStore = reactive({
    selectedTenantId: 'tenant-a', currentTenantId: 'home', currentTenantRole: 'admin',
    canAccessAllTenants: false,
    hasRole: (_role: string): boolean => authStore.currentTenantRole === 'admin',
  })
  const access = runInNewContext(`${providerCode}\n({
    load: loadExistingWkcAccess,
    allowed: () => canSeeSection('weknoracloud'),
    pending: () => isSectionSupportPending('weknoracloud'),
    snapshot: () => ({ ...wkcAccess.value }),
  })`, {
    computed, ref, authStore, isProviderHidden,
    visible: { value: true },
    getWeKnoraCloudStatus: status, listModels: models,
    isIntegrationSection: () => false,
    SYSTEM_ADMIN_SECTIONS: new Set(), SETTINGS_SECTION_MIN_ROLE,
  }) as { load: () => Promise<void>; allowed: () => boolean; pending: () => boolean; snapshot: () => { tenant: string; ready: boolean; existing: boolean } }
  return { authStore, ...access }
}

test('hidden cloud maintenance remains unavailable without credentials or models', async () => {
  const access = providerAccessFixture(async () => ({ has_models: false, needs_reinit: false }), async () => [])
  assert.equal(access.pending(), true)
  assert.equal(access.allowed(), false)
  await access.load()
  assert.equal(access.pending(), false)
  assert.equal(access.allowed(), false)
})

test('existing and expired cloud credentials keep their maintenance page available to admins', async () => {
  for (const needs_reinit of [false, true]) {
    const access = providerAccessFixture(async () => ({ has_models: true, needs_reinit }), async () => [])
    await access.load()
    assert.equal(access.allowed(), true)
    access.authStore.currentTenantRole = 'viewer'
    assert.equal(access.allowed(), false)
  }
})

test('existing cloud models allow recovery when credentials are absent', async () => {
  const access = providerAccessFixture(
    async () => ({ has_models: false, needs_reinit: false }),
    async () => [{ parameters: { provider: 'weknoracloud' } }],
  )
  await access.load()
  assert.equal(access.allowed(), true)
})

test('failed metadata checks do not expose an unused cloud setup page', async () => {
  const fail = async (): Promise<never> => { throw new Error('unavailable') }
  const access = providerAccessFixture(fail, fail)
  await access.load()
  assert.equal(access.allowed(), false)
  assert.equal(access.pending(), false)
})

test('a late metadata response cannot reopen cloud maintenance in another workspace', async () => {
  let resolveOld!: (value: { has_models: boolean; needs_reinit: boolean }) => void
  const delayed = new Promise<{ has_models: boolean; needs_reinit: boolean }>(resolve => { resolveOld = resolve })
  let checks = 0
  const access = providerAccessFixture(
    () => ++checks === 1 ? delayed : Promise.resolve({ has_models: false, needs_reinit: false }),
    async () => [],
  )
  const previous = access.load()
  access.authStore.selectedTenantId = 'tenant-b'
  assert.equal(access.allowed(), false)
  await access.load()
  resolveOld({ has_models: true, needs_reinit: false })
  await previous
  assert.equal(access.allowed(), false)
  assert.equal(access.snapshot().tenant, 'tenant-b')
})
