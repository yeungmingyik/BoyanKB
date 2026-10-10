import type { ComposerTranslation } from 'vue-i18n'

type BusinessDocumentEvent = {
  pending?: boolean
  success?: boolean
  arguments?: unknown
  tool_data?: Record<string, unknown> | null
}

const states = new Set(['awaiting_confirmation', 'approved', 'generating', 'reviewing', 'published', 'blocked', 'failed', 'superseded'])

export function getBusinessDocumentToolTitle(t: ComposerTranslation, event: BusinessDocumentEvent): string {
  if (event.pending) return t('businessSkills.fileWorking')
  if (event.success !== true) return t('businessSkills.fileFailed')
  const state = event.tool_data?.state
  if (typeof state === 'string' && states.has(state)) return t(`businessSkills.fileStates.${state}`)
  let args = event.arguments
  if (typeof args === 'string') {
    try { args = JSON.parse(args) } catch { args = null }
  }
  if (args && typeof args === 'object' && 'operation' in args && args.operation === 'read_skill') return t('businessSkills.fileRead')
  return t('businessSkills.fileDone')
}
