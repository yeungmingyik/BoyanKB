<template>
  <main class="team-page">
    <header class="team-page__header">
      <div><h1>{{ t('businessTeam.title') }}</h1><p>{{ t('businessTeam.workspace', { name: auth.currentTenantName }) }}</p></div>
      <t-button variant="outline" theme="default" :loading="team.loading" :disabled="team.busy" @click="refresh">
        <template #icon><t-icon name="refresh" /></template>{{ t('businessTeam.refresh') }}
      </t-button>
    </header>
    <div v-if="team.loading" class="team-page__notice" role="status"><t-loading :text="t('common.loading')" /></div>
    <p v-else-if="team.failed" class="team-page__notice" role="alert">{{ t('businessTeam.loadFailed') }}</p>
    <p v-else-if="!team.available" class="team-page__notice" role="status">{{ t(team.error === 'denied' ? 'businessTeam.denied' : 'businessTeam.unavailable') }}</p>
    <template v-else>
      <section class="team-page__section" aria-labelledby="team-invite-heading">
        <h2 id="team-invite-heading">{{ t('businessTeam.invite') }}</h2>
        <p>{{ t('businessTeam.description') }}</p>
        <p class="team-page__permissions"><t-tag theme="default" variant="light">{{ t('businessTeam.viewer') }}</t-tag>{{ t('businessTeam.permissions') }}</p>
        <form class="team-page__form" @submit.prevent="create">
          <t-radio-group v-model="kind" :disabled="team.busy" variant="default" :aria-label="t('businessTeam.invite')">
            <t-radio-button value="link">{{ t('businessTeam.link') }}</t-radio-button>
            <t-radio-button value="email">{{ t('businessTeam.email') }}</t-radio-button>
          </t-radio-group>
          <p id="team-invite-hint" class="team-page__muted">{{ t(kind === 'link' ? 'businessTeam.linkHint' : 'businessTeam.emailHint') }}</p>
          <div v-if="kind === 'email'" class="team-page__field">
            <label for="team-invite-email">{{ t('businessTeam.emailLabel') }}</label>
            <input id="team-invite-email" v-model.trim="email" type="email" required maxlength="254" autocomplete="off" :disabled="team.busy" :placeholder="t('businessTeam.emailPlaceholder')" aria-describedby="team-invite-hint" />
          </div>
          <t-button theme="primary" type="submit" :loading="team.busy" :disabled="kind === 'email' && !email.trim()">{{ t(kind === 'link' ? 'businessTeam.createLink' : 'businessTeam.createEmail') }}</t-button>
        </form>
        <p v-if="team.error" role="alert">{{ t(`businessTeam.${team.error}`) }}</p>
        <div v-if="team.created" class="team-page__created" role="status">
          <h3>{{ t('businessTeam.created') }}</h3>
          <template v-if="team.created.kind === 'link'">
            <div v-if="inviteUrl" class="team-page__copy">
              <input :value="inviteUrl" readonly :aria-label="t('businessTeam.link')" spellcheck="false" />
              <t-button theme="primary" variant="outline" @click="copyLink">{{ t('businessTeam.copy') }}</t-button>
            </div>
            <p>{{ t(inviteUrl ? 'businessTeam.linkOnce' : 'businessTeam.invalidLink') }}</p>
          </template>
          <p v-else>{{ t('businessTeam.emailCreated') }}</p>
        </div>
      </section>
      <section class="team-page__section" aria-labelledby="team-members-heading">
        <h2 id="team-members-heading">{{ t('businessTeam.members') }} <span>{{ team.members.length }}</span></h2>
        <p v-if="!team.members.length" class="team-page__empty">{{ t('businessTeam.membersEmpty') }}</p>
        <ul v-else class="team-page__list">
          <li v-for="member in team.members" :key="member.user_id"><span class="team-page__name">{{ member.username }}</span><t-tag theme="default" variant="light">{{ t('businessTeam.viewer') }}</t-tag></li>
        </ul>
      </section>
      <section class="team-page__section" aria-labelledby="team-invitations-heading">
        <h2 id="team-invitations-heading">{{ t('businessTeam.invitations') }}</h2>
        <p v-if="!team.invitations.length" class="team-page__empty">{{ t('businessTeam.invitationsEmpty') }}</p>
        <ul v-else class="team-page__list">
          <li v-for="invitation in team.invitations" :key="invitation.id" class="team-page__invitation">
            <div class="team-page__invitation-body">
              <h3>{{ invitation.kind === 'link' ? t('businessTeam.link') : invitation.invitee_name || t('businessTeam.invitee') }}</h3>
              <p>{{ t('businessTeam.createdAt', { date: date(invitation.created_at) }) }}</p>
              <p>{{ t('businessTeam.expires', { date: date(invitation.expires_at) }) }}<span v-if="invitation.kind === 'link'"> · {{ t('businessTeam.joined', { count: invitation.accepted_count }) }}</span></p>
            </div>
            <div class="team-page__invitation-actions">
              <t-tag theme="default" variant="light">{{ t(`businessTeam.status.${status(invitation)}`) }}</t-tag>
              <t-button v-if="canRevokeTeamInvitation(invitation)" theme="danger" variant="text" :disabled="team.busy" @click="confirmRevoke(invitation.id)">{{ t('businessTeam.revoke') }}</t-button>
            </div>
          </li>
        </ul>
        <p v-if="team.total > team.invitations.length" class="team-page__muted">{{ t('businessTeam.partial', { count: team.invitations.length, total: team.total }) }}</p>
      </section>
    </template>
  </main>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { DialogPlugin, MessagePlugin } from 'tdesign-vue-next'
import type { BusinessTeamInvitation } from '@/api/business-team'
import { canRevokeTeamInvitation, teamInviteUrl } from '@/composables/businessTeam'
import { useBusinessTeamStore } from '@/stores/businessTeam'
import { useAuthStore } from '@/stores/auth'

const { t, locale } = useI18n()
const auth = useAuthStore()
const team = useBusinessTeamStore()
const kind = ref<'link' | 'email'>('link')
const email = ref('')
const inviteUrl = computed(() => team.available ? teamInviteUrl(team.created?.invite_url, window.location.origin) : '')
let confirmation: ReturnType<typeof DialogPlugin.confirm> | undefined
const refresh = () => { confirmation?.destroy(); confirmation = undefined; void team.load() }
const date = (value: string) => Number.isFinite(Date.parse(value)) ? new Intl.DateTimeFormat(locale.value, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—'
const status = (value: BusinessTeamInvitation) => value.status === 'pending' && Date.parse(value.expires_at) <= Date.now() ? 'expired' : value.status

async function create() {
  const accepted = await team.create({ kind: kind.value, ...(kind.value === 'email' ? { email: email.value } : {}) })
  if (accepted) email.value = ''
}

async function copyLink() {
  const value = inviteUrl.value
  if (!value) return
  try {
    await navigator.clipboard.writeText(value)
    if (inviteUrl.value === value) MessagePlugin.success(t('businessTeam.copied'))
  } catch {
    if (inviteUrl.value === value) MessagePlugin.warning(t('businessTeam.copyFailed'))
  }
}

function confirmRevoke(id: number) {
  const tenantId = auth.effectiveTenantId
  const userId = auth.currentUserId
  confirmation = DialogPlugin.confirm({
    header: t('businessTeam.revokeTitle'), body: t('businessTeam.revokeHint'), confirmBtn: t('businessTeam.revoke'), cancelBtn: t('businessTeam.cancel'),
    onConfirm: async () => {
      confirmation?.destroy()
      confirmation = undefined
      if (tenantId !== auth.effectiveTenantId || userId !== auth.currentUserId) return
      if (await team.revoke(id)) MessagePlugin.success(t('businessTeam.revoked'))
    },
    onClose: () => { confirmation?.destroy(); confirmation = undefined },
  })
}

watch(() => [auth.effectiveTenantId, auth.currentUserId] as const, () => {
  email.value = ''
  kind.value = 'link'
  confirmation?.destroy()
  confirmation = undefined
  void team.load()
}, { flush: 'post' })
onMounted(refresh)
onBeforeUnmount(() => { confirmation?.destroy(); void team.load(false) })
</script>

<style scoped lang="less">
.team-page {
  box-sizing: border-box;
  width: 100%;
  flex: 1;
  min-width: 0;
  min-height: 0;
  max-width: 1120px;
  margin: 0 auto;
  padding: var(--app-space-8) var(--app-space-6);
  color: var(--td-text-color-primary);
  overflow-wrap: anywhere;
  overflow-y: auto;

  h1 { margin: 0; font-size: 24px; line-height: 1.4; }
  h2 { margin: 0 0 var(--app-space-4); font-size: 18px; line-height: 1.5; }
  h2 span { margin-left: var(--app-space-2); color: var(--td-text-color-secondary); font-size: 14px; font-weight: 400; }
  h3 { margin: 0; font-size: 14px; line-height: 1.6; font-weight: 600; }
  p { margin: var(--app-space-2) 0; line-height: 1.7; }
  input { box-sizing: border-box; width: 100%; min-width: 0; padding: 9px 12px; border: 1px solid var(--td-border-level-2-color); border-radius: var(--td-radius-default); background: var(--td-bg-color-container); color: var(--td-text-color-primary); font: inherit; }
  input:focus-visible { outline: 2px solid var(--td-brand-color); outline-offset: 2px; }
  input::placeholder { color: var(--td-text-color-placeholder); }
}
.team-page__header { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: flex-start; gap: var(--app-space-4); margin-bottom: var(--app-space-6); }
.team-page__header > div { min-width: 0; }
.team-page__header p, .team-page__muted { color: var(--td-text-color-secondary); }
.team-page__section { padding: var(--app-space-6) 0; border-top: 1px solid var(--td-component-stroke); }
.team-page__permissions { display: flex; gap: var(--app-space-3); align-items: flex-start; color: var(--td-text-color-secondary); max-width: 76ch; }
.team-page__permissions :deep(.t-tag) { flex-shrink: 0; margin-top: 3px; }
.team-page__form { margin-top: var(--app-space-6); max-width: 680px; }
.team-page__form > .t-button { margin-top: var(--app-space-4); }
.team-page__field { display: grid; gap: var(--app-space-2); margin-top: var(--app-space-4); }
.team-page__created { margin-top: var(--app-space-5); padding: var(--app-space-4); border-radius: var(--td-radius-default); background: var(--td-bg-color-secondarycontainer); }
.team-page__created p { color: var(--td-text-color-secondary); font-size: 13px; }
.team-page__copy { display: flex; gap: var(--app-space-3); margin: var(--app-space-3) 0; }
.team-page__copy input { flex: 1; }
.team-page__copy .t-button { flex-shrink: 0; }
.team-page__list { list-style: none; margin: 0; padding: 0; }
.team-page__list > li { display: flex; justify-content: space-between; align-items: center; gap: var(--app-space-4); padding: var(--app-space-4) 0; border-bottom: 1px solid var(--td-component-stroke); }
.team-page__list > li:last-child { border-bottom: 0; }
.team-page__name, .team-page__invitation-body { min-width: 0; }
.team-page__invitation-body p { font-size: 13px; color: var(--td-text-color-secondary); }
.team-page__invitation-actions { display: flex; align-items: center; gap: var(--app-space-3); flex-shrink: 0; }
.team-page__empty, .team-page__notice { color: var(--td-text-color-secondary); padding: var(--app-space-5) 0; }
@media (max-width: 640px) {
  .team-page { padding: var(--app-space-5) var(--app-space-4); }
  .team-page__header { gap: var(--app-space-2); }
  .team-page__header h1 { font-size: 20px; }
  .team-page__permissions { display: block; }
  .team-page__permissions :deep(.t-tag) { margin-right: var(--app-space-2); }
  .team-page__form :deep(.t-radio-group) { display: flex; width: 100%; flex-wrap: wrap; }
  .team-page__form :deep(.t-radio-button) { min-width: 0; height: auto; white-space: normal; flex: 1; }
  .team-page__form > .t-button { max-width: 100%; height: auto; min-height: 32px; white-space: normal; }
  .team-page__copy { flex-direction: column; }
  .team-page__list > .team-page__invitation { flex-direction: column; align-items: stretch; gap: var(--app-space-2); }
  .team-page__invitation-actions { justify-content: space-between; }
}
</style>
