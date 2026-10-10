import { watch } from 'vue'
import { defineStore } from 'pinia'
import { useAuthStore } from './auth'
import { createBusinessTeamState } from '@/composables/businessTeam'
import { getBusinessTeam, listBusinessTeamInvitations, createBusinessTeamInvitation, revokeBusinessTeamInvitation } from '@/api/business-team'

export const useBusinessTeamStore = defineStore('businessTeam', () => {
  const auth = useAuthStore()
  const state = createBusinessTeamState({ access: getBusinessTeam, list: listBusinessTeamInvitations, create: createBusinessTeamInvitation, revoke: revokeBusinessTeamInvitation })
  watch(() => [auth.currentUserId, auth.effectiveTenantId, auth.isLoggedIn] as const, ([userId, tenantId, loggedIn]) => {
    void state.setScope(loggedIn && userId && Number(tenantId) > 0 ? { userId, tenantId: Number(tenantId) } : null)
  }, { immediate: true, flush: 'sync' })
  return state
})
