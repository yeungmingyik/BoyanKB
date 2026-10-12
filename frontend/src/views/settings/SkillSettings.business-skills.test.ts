import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'
import { allowsNativeSkillInstall, businessSkillLabel, BUSINESS_SKILL_LABELS } from '../../config/businessSkills'

const source = readFileSync(new URL('./SkillSettings.vue', import.meta.url), 'utf8')
const script = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)![1]!
const ast = ts.createSourceFile('SkillSettings.ts', script, ts.ScriptTarget.Latest, true)

function functions(names: string[], context: Record<string, unknown>) {
  const body = names.map((name) => {
    const declaration = ast.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === name)
    assert.ok(declaration, name)
    return declaration.getText(ast)
  }).join('\n')
  return runInNewContext(ts.transpileModule(`${body}\n({ ${names.join(', ')} })`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText, { allowsNativeSkillInstall, nativeExecution: true, canManage: { value: true }, ...context })
}

const sandbox = { id: 'sandbox-a', name: 'Sandbox A', sandbox_type: 'docker' }
const entry = (name: string, installed = false) => ({
  id: 'catalog-a', name,
  installations: installed ? [{ sandbox_config_id: sandbox.id, status: 'ready', enabled: true }] : [],
})

test('members cannot open management drawers or submit mutations through stale handlers', async () => {
  const management = { value: false }
  const methods = functions([
    'liveInstalls', 'canDelete', 'targetsFor', 'defaultAddTargets', 'openCatalogFiles',
    'openAdd', 'openInstall', 'openInstallTo', 'openUpgrade', 'openManage',
    'registerThenAdvance', 'handleAddPrimary', 'confirmInstall', 'removeCatalog',
  ], {
    canManage: management,
    businessSkillLabel,
    MessagePlugin: { warning: () => {} },
    t: (key: string) => key,
  })
  const item = entry('third-party')
  assert.equal(methods.canDelete(item), false)
  assert.equal(methods.targetsFor(item).length, 0)
  assert.equal(methods.defaultAddTargets().length, 0)
  methods.openCatalogFiles(item)
  await methods.openAdd()
  methods.openInstall(item)
  methods.openInstallTo(item, sandbox)
  methods.openUpgrade(item)
  methods.openManage(item, {})
  await methods.registerThenAdvance()
  await methods.handleAddPrimary()
  await methods.confirmInstall()
  await methods.removeCatalog(item)
  management.value = true
  assert.equal(methods.canDelete(item), true)
  assert.equal(methods.canDelete(entry('boyan-pptx')), false)
})

test('all nine business guide IDs stay out of native install and upgrade choices even with a sandbox', () => {
  const methods = functions(['liveInstalls', 'targetsFor', 'unusedTargets', 'installsView', 'sandboxPickRows'], {
    skillConfigs: { value: [sandbox] },
    installPriority: () => 0,
    installName: () => sandbox.name,
    installUpgradable: () => true,
    isInstallBusy: () => false,
    upgradeVersionText: () => 'new-version',
  })
  for (const name of Object.keys(BUSINESS_SKILL_LABELS)) {
    assert.equal(allowsNativeSkillInstall(name), false)
    for (const installed of [false, true]) {
      const item = entry(name, installed)
      assert.equal(methods.targetsFor(item).length, 0, name)
      assert.equal(methods.unusedTargets(item).length, 0, name)
      assert.equal(methods.installsView(item).canAdd, false, name)
      assert.equal(methods.installsView(item).upgradable.length, 0, name)
      for (const mode of ['all', 'remaining', 'upgrade']) assert.equal(methods.sandboxPickRows(item, mode, []).length, 0, name)
    }
  }
  for (const name of ['third-party', 'boyan-pptx-extra', 'toString']) {
    assert.equal(allowsNativeSkillInstall(name), true)
    assert.equal(methods.targetsFor(entry(name)).length, 1)
    assert.equal(methods.installsView(entry(name)).canAdd, true)
    assert.equal(methods.installsView(entry(name, true)).upgradable.length, 1)
    assert.equal(methods.sandboxPickRows(entry(name), 'all', []).length, 1)
  }
})

test('business guides cannot reopen native dialogs or submit installs through stale selections', async () => {
  let outgoing = 0
  let modelLoads = 0
  const selected = { value: entry('third-party') }
  const showInstall = { value: false }
  const showManage = { value: false }
  const showAdd = { value: true }
  const methods = functions(['openInstall', 'openInstallTo', 'openUpgrade', 'openManage', 'defaultAddTargets', 'confirmInstall', 'handleAddPrimary'], {
    registeredCatalog: selected,
    installCatalog: selected,
    installTargetIds: { value: ['sandbox-a'] },
    addTargetIds: { value: ['sandbox-a'] },
    addPrimaryLoading: { value: false },
    addPrimaryDisabled: { value: false },
    addStep: { value: 1 },
    showInstall, showManage, showAdd,
    skillConfigs: { value: [sandbox] },
    props: { initialSandboxId: 'sandbox-a' },
    loadInstallerModel: () => { modelLoads++ },
    installSkillCatalog: async () => { outgoing++; return { data: {} } },
    installPickRows: { value: [] },
    pickedOnlyUpgrades: () => false,
    installing: { value: false },
    ensureInstallerModelIfNeeded: async () => {},
    catalogInstallFailedCount: () => 0,
    MessagePlugin: { success: () => {} },
    t: (key: string) => key,
    rememberSession: () => {},
    installSessionIds: { value: [] },
    loadCatalog: async () => {},
    prunePicks: () => {},
  })
  for (const name of Object.keys(BUSINESS_SKILL_LABELS)) {
    selected.value = entry(name)
    showAdd.value = true
    methods.openInstall(selected.value)
    methods.openInstallTo(selected.value, sandbox)
    methods.openUpgrade(selected.value)
    methods.openManage(selected.value, {})
    assert.equal(methods.defaultAddTargets().length, 0)
    await methods.confirmInstall()
    await methods.handleAddPrimary()
    assert.equal(showInstall.value, false)
    assert.equal(showManage.value, false)
    assert.equal(showAdd.value, false)
  }
  assert.equal(outgoing, 0)
  assert.equal(modelLoads, 0)
  selected.value = entry('third-party')
  assert.equal(methods.defaultAddTargets().length, 1)
  await methods.confirmInstall()
  assert.equal(outgoing, 1)
})
