<template>
  <div class="business-tool-result">
    <p>{{ getBusinessDocumentToolTitle(t, event) }}</p>
    <details v-if="event.output">
      <summary>{{ t('businessSkills.technical') }}</summary>
      <pre>{{ event.output }}</pre>
    </details>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { getBusinessDocumentToolTitle } from '@/utils/business-document-display'

defineProps<{
  event: {
    success?: boolean
    pending?: boolean
    arguments?: unknown
    tool_data?: Record<string, unknown> | null
    output?: string
  }
}>()
const { t } = useI18n()
</script>

<style scoped>
.business-tool-result { min-width: 0; line-height: 1.65; }
p { margin: 0 0 8px; }
summary { cursor: pointer; min-height: 36px; align-content: center; color: var(--td-text-color-secondary); }
summary:focus-visible { outline: 2px solid var(--td-brand-color); outline-offset: 2px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 360px; overflow-y: auto; font-size: var(--app-text-sm); }
</style>
