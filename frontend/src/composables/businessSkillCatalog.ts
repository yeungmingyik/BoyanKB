import { ref } from 'vue'
import type { BusinessSkillCatalog, BusinessSkillDetail, BusinessSkillResponse } from '../api/business-skills'
import { canReadBusinessSkill } from '../config/businessSkills'

export function createBusinessSkillCatalog(api: {
  list: () => Promise<BusinessSkillResponse<BusinessSkillCatalog>>
  detail: (name: string) => Promise<BusinessSkillResponse<BusinessSkillDetail>>
}) {
  const catalog = ref<BusinessSkillCatalog | null>(null)
  const loading = ref(false)
  const failed = ref(false)
  const detail = ref<BusinessSkillDetail | null>(null)
  const detailLoading = ref('')
  const detailFailed = ref('')
  let generation = 0
  let detailGeneration = 0

  const clear = () => {
    generation++
    detailGeneration++
    catalog.value = null
    detail.value = null
    detailLoading.value = ''
    detailFailed.value = ''
    loading.value = false
    failed.value = false
  }

  const load = async () => {
    clear()
    const current = generation
    loading.value = true
    try {
      const response = await api.list()
      if (current !== generation) return
      if (!response.success || !response.data || !Array.isArray(response.data.skills)) throw new Error('invalid_catalog')
      catalog.value = response.data
    } catch {
      if (current === generation) failed.value = true
    } finally {
      if (current === generation) loading.value = false
    }
  }

  const read = async (name: string) => {
    const skill = catalog.value?.skills.find((item) => item.name === name)
    if (!skill || !canReadBusinessSkill(skill)) return
    const current = generation
    const request = ++detailGeneration
    detail.value = null
    detailLoading.value = name
    detailFailed.value = ''
    try {
      const response = await api.detail(name)
      if (current !== generation || request !== detailGeneration) return
      if (!response.success || response.data?.name !== name || !canReadBusinessSkill(response.data)) throw new Error('invalid_guide')
      detail.value = response.data
    } catch {
      if (current === generation && request === detailGeneration) detailFailed.value = name
    } finally {
      if (current === generation && request === detailGeneration) detailLoading.value = ''
    }
  }

  const close = () => {
    detailGeneration++
    detail.value = null
    detailLoading.value = ''
    detailFailed.value = ''
  }

  return { catalog, loading, failed, detail, detailLoading, detailFailed, load, read, close, clear }
}
