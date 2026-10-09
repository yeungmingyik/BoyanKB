import type { BusinessSkill, BusinessSkillCatalog } from '../api/business-skills'

export const BUSINESS_SKILL_LABELS: Record<string, string> = {
  'boyan-courseware': 'businessSkills.names.courseware',
  'boyan-event-plan': 'businessSkills.names.eventPlan',
  'boyan-local-promotion-plan': 'businessSkills.names.localPromotion',
  'boyan-cooperation-proposal': 'businessSkills.names.proposal',
  'boyan-faq-guide': 'businessSkills.names.faq',
  'boyan-docx': 'businessSkills.names.docx',
  'boyan-pptx': 'businessSkills.names.pptx',
  'boyan-xlsx': 'businessSkills.names.xlsx',
  'boyan-md': 'businessSkills.names.md',
}

export function businessSkillLabel(name: string): string | undefined {
  return Object.hasOwn(BUSINESS_SKILL_LABELS, name) ? BUSINESS_SKILL_LABELS[name] : undefined
}

export function allowsNativeSkillInstall(name: string): boolean {
  return !businessSkillLabel(name)
}

export function canReadBusinessSkill(skill: BusinessSkill): boolean {
  return skill.installed && skill.available && skill.hash_verified && skill.status === 'ready'
}

export function canUseBusinessPlanner(catalog: BusinessSkillCatalog | null): boolean {
  return Boolean(catalog?.installed && catalog.available && catalog.status === 'ready'
    && catalog.planner_agent_id && catalog.skills.length === Object.keys(BUSINESS_SKILL_LABELS).length
    && Object.keys(BUSINESS_SKILL_LABELS).every((name) => catalog.skills.some((skill) => skill.name === name && canReadBusinessSkill(skill))))
}

export function businessSkillGuide(content: string): string {
  return content.replace(/^\uFEFF?---\r?\n[\s\S]*?\r?\n---\r?\n/, '').trim()
}
