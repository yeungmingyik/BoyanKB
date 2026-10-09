import assert from 'node:assert/strict'
import test from 'node:test'
import type { BusinessSkillCatalog, BusinessSkillDetail, BusinessSkillResponse } from '../api/business-skills'
import { BUSINESS_SKILL_LABELS, businessSkillGuide, businessSkillLabel, canUseBusinessPlanner } from '../config/businessSkills'
import { createBusinessSkillCatalog } from './businessSkillCatalog'

function fixture(): BusinessSkillCatalog {
  return {
    installed: true, available: true, status: 'ready', reason: '', planner_agent_id: 'planner-a',
    skills: Object.keys(BUSINESS_SKILL_LABELS).map((name) => ({
      name, display_name: name, description: '', kind: 'scene', version: 'boyan/v1',
      installed: true, available: true, hash_verified: true, status: 'ready', reason: '',
    })),
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

const success = <T>(data: T): BusinessSkillResponse<T> => ({ success: true, data })
const guide = (): BusinessSkillDetail => ({ ...fixture().skills[0]!, content: '当前空间说明' })

test('registered names or sandbox status alone cannot enable the business planner', () => {
  const catalog = fixture()
  assert.equal(canUseBusinessPlanner(catalog), true)
  assert.equal(canUseBusinessPlanner({ ...catalog, installed: false, available: false, status: 'not_installed' }), false)
  assert.equal(canUseBusinessPlanner({ ...catalog, planner_agent_id: '' }), false)
  catalog.skills[0]!.hash_verified = false
  assert.equal(canUseBusinessPlanner(catalog), false)
  catalog.skills[0]!.hash_verified = true
  catalog.skills[0]!.name = catalog.skills[1]!.name
  assert.equal(canUseBusinessPlanner(catalog), false)
})

test('switching workspaces immediately clears planner and guide state and ignores old requests', async () => {
  const oldList = deferred<BusinessSkillResponse<BusinessSkillCatalog>>()
  const newList = deferred<BusinessSkillResponse<BusinessSkillCatalog>>()
  let calls = 0
  const state = createBusinessSkillCatalog({ list: () => (++calls === 1 ? oldList.promise : newList.promise), detail: async () => success(guide()) })
  const oldLoad = state.load()
  const newLoad = state.load()
  oldList.resolve(success(fixture()))
  await oldLoad
  assert.equal(state.catalog.value, null)
  assert.equal(state.loading.value, true)
  const hq = { ...fixture(), planner_agent_id: undefined, installed: false, available: false, status: 'not_installed' as const }
  newList.resolve(success(hq))
  await newLoad
  assert.equal(canUseBusinessPlanner(state.catalog.value), false)
  assert.equal(state.loading.value, false)
})

test('guide responses from a previous workspace or a closed dialog cannot reopen it', async () => {
  let result = deferred<BusinessSkillResponse<BusinessSkillDetail>>()
  const state = createBusinessSkillCatalog({ list: async () => success(fixture()), detail: () => result.promise })
  await state.load()
  const first = state.read('boyan-courseware')
  state.clear()
  result.resolve(success(guide()))
  await first
  assert.equal(state.detail.value, null)
  assert.equal(state.catalog.value, null)
  await state.load()
  result = deferred<BusinessSkillResponse<BusinessSkillDetail>>()
  const second = state.read('boyan-courseware')
  state.close()
  result.resolve(success(guide()))
  await second
  assert.equal(state.detail.value, null)
  assert.equal(state.detailLoading.value, '')
})

test('unavailable and unknown guides do not issue detail requests', async () => {
  const catalog = fixture()
  catalog.skills[0]!.hash_verified = false
  let reads = 0
  const state = createBusinessSkillCatalog({ list: async () => success(catalog), detail: async () => { reads++; return success(guide()) } })
  await state.load()
  await state.read('boyan-courseware')
  await state.read('arbitrary-upload')
  assert.equal(reads, 0)
  assert.equal(state.detail.value, null)
})

test('failed responses hide prior available state and a retry can recover', async () => {
  let reject = false
  const state = createBusinessSkillCatalog({
    list: async () => { if (reject) throw new Error('offline'); return success(fixture()) },
    detail: async () => success({ ...guide(), hash_verified: false }),
  })
  await state.load()
  await state.read('boyan-courseware')
  assert.equal(state.detail.value, null)
  assert.equal(state.detailFailed.value, 'boyan-courseware')
  reject = true
  await state.load()
  assert.equal(state.failed.value, true)
  assert.equal(canUseBusinessPlanner(state.catalog.value), false)
  reject = false
  await state.load()
  assert.equal(state.failed.value, false)
  assert.equal(canUseBusinessPlanner(state.catalog.value), true)
})

test('only the exact nine registered names receive business labels; guide prose stays inert text', () => {
  assert.equal(businessSkillLabel('boyan-pptx'), 'businessSkills.names.pptx')
  for (const name of ['toString', '__proto__', 'boyan-pptx-extra', 'third-party']) assert.equal(businessSkillLabel(name), undefined)
  assert.equal(businessSkillGuide('---\nname: boyan-pptx\n---\n# 说明\n<script>run()</script>'), '# 说明\n<script>run()</script>')
})
