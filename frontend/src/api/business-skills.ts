import { get } from '@/utils/request'

export type BusinessSkillStatus = 'not_installed' | 'not_ready' | 'ready' | 'unavailable'

export interface BusinessSkill {
  name: string
  display_name: string
  description: string
  kind: 'scene' | 'format'
  version: string
  installed: boolean
  available: boolean
  hash_verified: boolean
  sha256?: string
  status: BusinessSkillStatus
  reason: string
}

export interface BusinessSkillCatalog {
  installed: boolean
  available: boolean
  status: BusinessSkillStatus
  reason: string
  planner_agent_id?: string
  skills: BusinessSkill[]
}

export interface BusinessSkillDetail extends BusinessSkill {
  content: string
}

export interface BusinessSkillResponse<T> {
  success: boolean
  data: T
}

const base = '/api/v1/business-workflow/skills'

export const listBusinessSkills = () => get<BusinessSkillResponse<BusinessSkillCatalog>>(base)
export const getBusinessSkill = (name: string) => get<BusinessSkillResponse<BusinessSkillDetail>>(`${base}/${encodeURIComponent(name)}`)
