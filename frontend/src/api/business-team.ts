import { getApiBaseUrl } from '@/utils/api-base'
import { createTeamTransport } from './team-transport'

export interface TeamScope {
  tenantId: number
  userId: string
}

export interface BusinessTeamMember {
  user_id: string
  username: string
  role: 'viewer'
}

export interface BusinessTeamAccess {
  available: boolean
  reason: string
  members?: BusinessTeamMember[]
}

export interface BusinessTeamInvitation {
  id: number
  kind: 'link' | 'email'
  invitee_name?: string
  status: 'pending' | 'accepted' | 'declined' | 'revoked' | 'expired'
  expires_at: string
  created_at: string
  accepted_count: number
}

export interface CreatedTeamInvitation extends BusinessTeamInvitation {
  invite_url?: string
  token?: string
}

export interface TeamInvitationInput {
  kind: 'link' | 'email'
  email?: string
}

export interface TeamResponse<T> {
  success: boolean
  data: T
}

export interface TeamInvitationList {
  items: BusinessTeamInvitation[]
  total: number
}

const request = createTeamTransport({ base: getApiBaseUrl, token: () => localStorage.getItem('weknora_token'), fetch: (...args) => fetch(...args) })

export const getBusinessTeam = (scope: TeamScope) => request<BusinessTeamAccess>(scope)
export const listBusinessTeamInvitations = (scope: TeamScope) => request<TeamInvitationList>(scope, '/invitations')
export const createBusinessTeamInvitation = (scope: TeamScope, input: TeamInvitationInput) => request<CreatedTeamInvitation>(scope, '/invitations', 'POST', { kind: input.kind, ...(input.kind === 'email' ? { email: input.email } : {}) })
export const revokeBusinessTeamInvitation = (scope: TeamScope, id: number) => {
  if (!Number.isSafeInteger(id) || id <= 0) throw new Error('TEAM_INVITATION_ID_INVALID')
  return request<undefined>(scope, `/invitations/${id}`, 'DELETE')
}
