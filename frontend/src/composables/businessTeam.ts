import { ref } from 'vue'
import type { BusinessTeamAccess, BusinessTeamInvitation, BusinessTeamMember, CreatedTeamInvitation, TeamInvitationInput, TeamInvitationList, TeamResponse, TeamScope } from '../api/business-team'

interface TeamApi {
  access: (scope: TeamScope) => Promise<TeamResponse<BusinessTeamAccess>>
  list: (scope: TeamScope) => Promise<TeamResponse<TeamInvitationList>>
  create: (scope: TeamScope, input: TeamInvitationInput) => Promise<TeamResponse<CreatedTeamInvitation>>
  revoke: (scope: TeamScope, id: number) => Promise<TeamResponse<undefined>>
}

export function teamInviteUrl(value: unknown, origin: string): string {
  if (typeof value !== 'string' || !value.startsWith('/register?')) return ''
  try {
    const url = new URL(value, origin)
    return url.origin === origin && url.pathname === '/register' && url.searchParams.getAll('token').length === 1 && url.searchParams.get('token') && !url.hash && [...url.searchParams.keys()].every((key) => key === 'token')
      ? url.href : ''
  } catch {
    return ''
  }
}

export function canRevokeTeamInvitation(value: BusinessTeamInvitation, now = Date.now()): boolean {
  return value.status === 'pending' && Number.isFinite(Date.parse(value.expires_at)) && Date.parse(value.expires_at) > now
}

export function createBusinessTeamState(api: TeamApi) {
  const available = ref(false)
  const members = ref<BusinessTeamMember[]>([])
  const invitations = ref<BusinessTeamInvitation[]>([])
  const total = ref(0)
  const loading = ref(false)
  const failed = ref(false)
  const busy = ref(false)
  const error = ref('')
  const created = ref<CreatedTeamInvitation | null>(null)
  let scope: TeamScope | null = null
  let epoch = 0

  function clear() {
    epoch++
    available.value = false
    members.value = []
    invitations.value = []
    total.value = 0
    loading.value = false
    failed.value = false
    busy.value = false
    error.value = ''
    created.value = null
  }

  function setScope(value: TeamScope | null) {
    clear()
    scope = value ? { ...value } : null
    return load(false)
  }

  async function load(withInvitations = true) {
    clear()
    if (!scope) return
    const current = epoch
    const target = { ...scope }
    loading.value = true
    try {
      const result = await api.access(target)
      if (current !== epoch) return
      if (!result.success || result.data?.available !== true) return
      available.value = true
      members.value = (result.data.members ?? []).filter((member) => member.role === 'viewer')
      if (withInvitations) {
        const listed = await api.list(target)
        if (current !== epoch) return
        if (!listed.success || !Array.isArray(listed.data?.items)) throw new Error('TEAM_LIST_FAILED')
        invitations.value = listed.data.items
        total.value = listed.data.total
      }
    } catch {
      if (current !== epoch) return
      available.value = false
      members.value = []
      invitations.value = []
      failed.value = true
    } finally {
      if (current === epoch) loading.value = false
    }
  }

  async function mutate(action: (target: TeamScope) => Promise<unknown>) {
    if (!scope || !available.value || loading.value || busy.value) return false
    const current = epoch
    const target = { ...scope }
    busy.value = true
    error.value = ''
    try {
      await action(target)
      return current === epoch
    } catch (failure) {
      if (current === epoch) {
        if ((failure as { status?: number }).status === 403 || (failure as { status?: number }).status === 401) {
          available.value = false
          members.value = []
          invitations.value = []
          created.value = null
          error.value = 'denied'
        } else {
          error.value = 'mutationFailed'
        }
      }
      return false
    } finally {
      if (current === epoch) busy.value = false
    }
  }

  async function create(input: TeamInvitationInput) {
    const current = epoch
    return mutate(async (target) => {
      const result = await api.create(target, { kind: input.kind, ...(input.kind === 'email' ? { email: input.email?.trim() } : {}) })
      if (current !== epoch) return
      if (!result.success || !result.data?.id) throw new Error('TEAM_CREATE_FAILED')
      const { token: _token, invite_url: _url, ...item } = result.data
      created.value = { ...item, ...(item.kind === 'link' ? { invite_url: _url } : {}) }
      invitations.value = [item, ...invitations.value.filter((existing) => existing.id !== item.id)]
      total.value = Math.max(invitations.value.length, total.value + 1)
    })
  }

  async function revoke(id: number) {
    const item = invitations.value.find((invitation) => invitation.id === id)
    if (!item || !canRevokeTeamInvitation(item)) return false
    const current = epoch
    return mutate(async (target) => {
      const result = await api.revoke(target, id)
      if (current !== epoch) return
      if (!result.success) throw new Error('TEAM_REVOKE_FAILED')
      invitations.value = invitations.value.map((invitation) => invitation.id === id ? { ...invitation, status: 'revoked' } : invitation)
      if (created.value?.id === id) created.value = null
    })
  }

  return { available, members, invitations, total, loading, failed, busy, error, created, setScope, load, create, revoke, clear }
}
