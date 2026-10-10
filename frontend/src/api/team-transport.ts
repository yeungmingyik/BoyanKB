import type { TeamInvitationInput, TeamResponse, TeamScope } from './business-team'

export function createTeamTransport(options: { base: () => string; token: () => string | null; fetch: typeof fetch }) {
  return async function request<T>(scope: TeamScope, path = '', method = 'GET', body?: TeamInvitationInput): Promise<TeamResponse<T>> {
    const token = options.token()
    if (!token || !scope.userId || !Number.isSafeInteger(scope.tenantId) || scope.tenantId <= 0) throw new Error('TEAM_AUTH_REQUIRED')
    if (!['GET', 'POST', 'DELETE'].includes(method) || !/^$|^\/invitations(?:\/[1-9][0-9]*)?$/.test(path)) throw new Error('TEAM_REQUEST_INVALID')
    const response = await options.fetch(`${options.base()}/api/v1/business-workflow/team${path}`, {
      method,
      headers: { Authorization: `Bearer ${token}`, 'X-Tenant-ID': String(scope.tenantId), 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'same-origin',
      cache: 'no-store',
      redirect: 'error',
    })
    if (!response.ok) throw Object.assign(new Error('TEAM_REQUEST_FAILED'), { status: response.status })
    const result = await response.json() as TeamResponse<T>
    if (result.success !== true) throw new Error('TEAM_REQUEST_FAILED')
    return result
  }
}
