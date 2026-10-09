<template>
  <div class="business-skills">
    <div class="business-skills__toolbar">
      <p>{{ t('businessSkills.workspace', { name: authStore.currentTenantName }) }}</p>
      <t-button variant="text" theme="default" :loading="loading" @click="load">
        <template #icon><t-icon name="refresh" /></template>
        {{ t('businessSkills.refresh') }}
      </t-button>
    </div>

    <div v-if="loading" class="business-skills__loading" role="status">
      <t-loading :text="t('common.loading')" />
    </div>
    <div v-else-if="failed" class="business-skills__notice" role="alert">
      <p>{{ t('businessSkills.loadFailed') }}</p>
      <t-button theme="primary" variant="outline" @click="load">{{ t('businessSkills.refresh') }}</t-button>
    </div>
    <template v-else-if="catalog">
      <div class="business-skills__intro" :class="{ 'is-ready': ready }">
        <div>
          <h3 v-if="catalog.status === 'not_installed'">{{ t('businessSkills.notInstalled') }}</h3>
          <h3 v-else>{{ catalog.reason }}</h3>
          <p v-if="!catalog.installed">{{ t('businessSkills.switchSpace') }}</p>
          <p v-else>{{ t('businessSkills.workflow') }}</p>
          <p class="business-skills__muted">{{ t('businessSkills.restricted') }}</p>
        </div>
        <t-button v-if="ready" theme="primary" @click="openPlanner">{{ t('businessSkills.launch') }}</t-button>
      </div>

      <section v-for="group in groups" :key="group.kind" class="business-skills__group" :aria-labelledby="`business-${group.kind}`">
        <h3 :id="`business-${group.kind}`">{{ group.title }} <span>{{ group.skills.length }}</span></h3>
        <ul>
          <li v-for="skill in group.skills" :key="skill.name" class="business-skills__row">
            <div class="business-skills__body">
              <div class="business-skills__heading">
                <h4>{{ skill.display_name }}</h4>
                <span class="business-skills__status" :class="{ 'is-ready': canReadBusinessSkill(skill) }">
                  {{ t(`businessSkills.status.${skill.status}`) }}
                </span>
              </div>
              <p>{{ skill.description }}</p>
              <p v-if="skill.installed && !canReadBusinessSkill(skill)" class="business-skills__reason">{{ skill.reason }}</p>
              <details class="business-skills__technical">
                <summary>{{ t('businessSkills.technical') }}</summary>
                <dl>
                  <dt>{{ t('businessSkills.identifier') }}</dt><dd><code>{{ skill.name }}</code></dd>
                  <dt>{{ t('businessSkills.version') }}</dt><dd>{{ skill.version }}</dd>
                  <dt>{{ t('businessSkills.verification') }}</dt><dd>{{ t(skill.hash_verified ? 'businessSkills.verified' : 'businessSkills.notVerified') }}</dd>
                  <template v-if="skill.sha256"><dt>SHA-256</dt><dd><code>{{ skill.sha256 }}</code></dd></template>
                </dl>
              </details>
            </div>
            <t-button theme="default" variant="outline" :disabled="!canReadBusinessSkill(skill)"
              :loading="detailLoading === skill.name" @click="read(skill.name)">
              {{ t('businessSkills.read') }}
            </t-button>
          </li>
        </ul>
      </section>
    </template>

    <t-dialog :visible="Boolean(detail || detailLoading || detailFailed)" :header="guideTitle"
      width="min(840px, calc(100vw - 32px))" :footer="false" @close="close">
      <div v-if="detailLoading" role="status"><t-loading :text="t('common.loading')" /></div>
      <p v-else-if="detailFailed" role="alert">{{ t('businessSkills.detailFailed') }}</p>
      <pre v-else-if="detail" class="business-skills__guide">{{ businessSkillGuide(detail.content) }}</pre>
      <div class="business-skills__dialog-footer">
        <t-button theme="default" variant="outline" @click="close">{{ t('businessSkills.close') }}</t-button>
      </div>
    </t-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { getBusinessSkill, listBusinessSkills } from '@/api/business-skills'
import { createBusinessSkillCatalog } from '@/composables/businessSkillCatalog'
import { businessSkillGuide, canReadBusinessSkill, canUseBusinessPlanner } from '@/config/businessSkills'
import { useAuthStore } from '@/stores/auth'
import { useSettingsStore } from '@/stores/settings'

const emit = defineEmits<{ count: [value: number] }>()
const { t } = useI18n()
const router = useRouter()
const authStore = useAuthStore()
const settings = useSettingsStore()
const { catalog, loading, failed, detail, detailLoading, detailFailed, load, read, close, clear } =
  createBusinessSkillCatalog({ list: listBusinessSkills, detail: getBusinessSkill })
const ready = computed(() => canUseBusinessPlanner(catalog.value))
const groups = computed(() => [
  { kind: 'scene', title: t('businessSkills.scene'), skills: catalog.value?.skills.filter((skill) => skill.kind === 'scene') ?? [] },
  { kind: 'format', title: t('businessSkills.format'), skills: catalog.value?.skills.filter((skill) => skill.kind === 'format') ?? [] },
])
const guideTitle = computed(() => detail.value?.display_name
  ?? catalog.value?.skills.find((skill) => skill.name === (detailLoading.value || detailFailed.value))?.display_name
  ?? t('businessSkills.read'))

const openPlanner = () => {
  if (!ready.value || !catalog.value?.planner_agent_id) return
  settings.selectAgent(catalog.value.planner_agent_id)
  settings.toggleAgent(true)
  void router.push('/platform/creatChat')
}

watch(() => authStore.effectiveTenantId, () => { void load() }, { immediate: true })
watch(catalog, (value) => emit('count', value?.skills.filter(canReadBusinessSkill).length ?? 0), { immediate: true })
onBeforeUnmount(clear)
</script>

<style scoped lang="less">
.business-skills {
  max-width: 1040px;
  padding-bottom: var(--app-space-8);
  color: var(--td-text-color-primary);

  p { margin: 0; line-height: 1.65; }

  &__toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--app-space-4);
    margin-bottom: var(--app-space-4);
    color: var(--td-text-color-secondary);

    p { overflow-wrap: anywhere; }
  }

  &__loading, &__notice { padding: var(--app-space-8) 0; }
  &__notice p { margin-bottom: var(--app-space-4); }

  &__intro {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--app-space-6);
    padding: var(--app-space-5);
    background: var(--td-bg-color-secondarycontainer);
    border: 1px solid var(--td-component-stroke);
    border-radius: var(--app-radius-md);

    h3 { margin: 0 0 var(--app-space-2); font-size: var(--app-text-lg); line-height: 1.5; }
    .t-button { flex-shrink: 0; }
    &.is-ready { border-color: var(--td-brand-color-light); }
  }

  &__muted { margin-top: var(--app-space-2) !important; color: var(--td-text-color-secondary); font-size: var(--app-text-sm); }

  &__group {
    margin-top: var(--app-space-8);
    h3 { margin: 0; font-size: var(--app-text-lg); font-weight: 600; }
    h3 span { margin-left: var(--app-space-2); color: var(--td-text-color-secondary); font-size: var(--app-text-sm); font-weight: 400; }
    ul { margin: var(--app-space-3) 0 0; padding: 0; list-style: none; }
  }

  &__row {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--app-space-5);
    padding: var(--app-space-5) 0;
    border-top: 1px solid var(--td-component-stroke);
    .t-button { flex-shrink: 0; }
  }

  &__body { min-width: 0; flex: 1; }
  &__body > p { color: var(--td-text-color-secondary); font-size: var(--app-text-base); }
  &__heading { display: flex; align-items: center; flex-wrap: wrap; gap: var(--app-space-3); margin-bottom: var(--app-space-1); }
  &__heading h4 { margin: 0; font-size: var(--app-text-md); font-weight: 600; }
  &__status { color: var(--td-text-color-secondary); font-size: var(--app-text-xs); }
  &__status.is-ready { color: var(--td-brand-color); }
  &__reason { margin-top: var(--app-space-1) !important; }

  &__technical {
    margin-top: var(--app-space-2);
    font-size: var(--app-text-xs);
    color: var(--td-text-color-secondary);
    summary { width: fit-content; cursor: pointer; }
    summary:focus-visible { outline: 2px solid var(--td-brand-color); outline-offset: 3px; }
    dl { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: var(--app-space-2) var(--app-space-4); margin: var(--app-space-3) 0 0; }
    dd { margin: 0; overflow-wrap: anywhere; }
  }

  &__guide { max-height: 62vh; overflow: auto; margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font-family: var(--app-font-family); font-size: var(--app-text-base); line-height: 1.8; }
  &__dialog-footer { display: flex; justify-content: flex-end; padding-top: var(--app-space-4); }
}

@media (max-width: 640px) {
  :global(.main:has(.business-skills)) { min-width: 0; }
  .business-skills__intro, .business-skills__row { flex-direction: column; gap: var(--app-space-3); }
  .business-skills__toolbar { gap: var(--app-space-2); align-items: flex-start; }
}
</style>
