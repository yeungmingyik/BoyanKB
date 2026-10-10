import assert from 'node:assert/strict'
import test from 'node:test'
import type { ComposerTranslation } from 'vue-i18n'
import { getBusinessDocumentToolTitle } from './business-document-display'

const t = ((key: string) => key) as ComposerTranslation

test('successful tool execution does not imply a published document', () => {
  assert.equal(getBusinessDocumentToolTitle(t, { success: true, tool_data: { state: 'awaiting_confirmation' } }), 'businessSkills.fileStates.awaiting_confirmation')
  assert.equal(getBusinessDocumentToolTitle(t, { success: true, tool_data: { state: 'blocked' } }), 'businessSkills.fileStates.blocked')
  assert.equal(getBusinessDocumentToolTitle(t, { success: true, tool_data: { state: 'published' } }), 'businessSkills.fileStates.published')
  assert.equal(getBusinessDocumentToolTitle(t, { success: true, tool_data: { state: 'unexpected' } }), 'businessSkills.fileDone')
})

test('failure and pending status override stale result data', () => {
  assert.equal(getBusinessDocumentToolTitle(t, { success: false, tool_data: { state: 'published' } }), 'businessSkills.fileFailed')
  assert.equal(getBusinessDocumentToolTitle(t, { pending: true, success: true, tool_data: { state: 'published' } }), 'businessSkills.fileWorking')
  assert.equal(getBusinessDocumentToolTitle(t, {}), 'businessSkills.fileFailed')
})

test('current and persisted guide calls display business wording without internal identifiers', () => {
  for (const args of [{ operation: 'read_skill', skill_name: 'boyan-courseware' }, '{"operation":"read_skill","skill_name":"boyan-courseware"}']) {
    assert.equal(getBusinessDocumentToolTitle(t, { success: true, arguments: args }), 'businessSkills.fileRead')
  }
  assert.equal(getBusinessDocumentToolTitle(t, { success: true, arguments: '{invalid', tool_data: { job_id: 'private-id', sha256: 'private-hash' } }), 'businessSkills.fileDone')
})
