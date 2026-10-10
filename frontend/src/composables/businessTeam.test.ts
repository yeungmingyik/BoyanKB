import assert from 'node:assert/strict'
import test from 'node:test'
import { createBusinessTeamState, canRevokeTeamInvitation, teamInviteUrl } from './businessTeam'
import { createTeamTransport } from '../api/team-transport'
import type { BusinessTeamAccess, CreatedTeamInvitation, TeamInvitationInput, TeamResponse, TeamScope } from '../api/business-team'

const scopeA = { tenantId: 10003, userId: 'viewer-a' }
const scopeB = { tenantId: 10004, userId: 'viewer-b' }
const success = <T>(data: T): TeamResponse<T> => ({ success: true, data })
const invitation = (id = 1): CreatedTeamInvitation => ({ id, kind: 'link', status: 'pending', expires_at: '2099-01-01T00:00:00Z', created_at: '2026-10-10T00:00:00Z', accepted_count: 0, token: 'fixture-secret', invite_url: '/register?token=fixture-secret' })
const access = (name = 'employee-a'): BusinessTeamAccess => ({ available: true, reason: '', members: [{ user_id: name, username: name, role: 'viewer' }] })
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { resolve, promise }
}
function api() {
  return { access: async (_scope: TeamScope) => success(access()), list: async (_scope: TeamScope) => success({ items: [], total: 0 }), create: async (_scope: TeamScope, _input: TeamInvitationInput) => success(invitation()), revoke: async (_scope: TeamScope, _id: number) => success(undefined) }
}

test('only server availability enables team management, never a role or member list', async () => {
  const source = api()
  let creates = 0
  let lists = 0
  source.access = async () => success({ ...access(), available: false })
  source.create = async () => { creates++; return success(invitation()) }
  source.list = async () => { lists++; return success({ items: [], total: 0 }) }
  const state = createBusinessTeamState(source)
  await state.setScope(scopeA)
  await state.load()
  assert.equal(state.available.value, false)
  assert.equal(state.members.value.length, 0)
  assert.equal(await state.create({ kind: 'link' }), false)
  assert.equal(creates, 0)
  assert.equal(lists, 0)
})

test('workspace switch clears member data and ignores an earlier access response', async () => {
  const old = deferred<TeamResponse<BusinessTeamAccess>>()
  const next = deferred<TeamResponse<BusinessTeamAccess>>()
  const source = api()
  source.access = (scope) => scope.tenantId === scopeA.tenantId ? old.promise : next.promise
  const state = createBusinessTeamState(source)
  const first = state.setScope(scopeA)
  const second = state.setScope(scopeB)
  old.resolve(success(access('old-private-employee')))
  await first
  assert.equal(state.members.value.length, 0)
  assert.equal(state.available.value, false)
  next.resolve(success(access('current-employee')))
  await second
  assert.equal(state.members.value[0]?.username, 'current-employee')
})

test('pending creation is bound to its original workspace and cannot reveal its link after a switch', async () => {
  const result = deferred<TeamResponse<CreatedTeamInvitation>>()
  const source = api()
  let requested: TeamScope | undefined
  source.create = (scope) => { requested = scope; return result.promise }
  const state = createBusinessTeamState(source)
  await state.setScope(scopeA)
  const mutation = state.create({ kind: 'link' })
  await state.setScope(scopeB)
  result.resolve(success(invitation()))
  assert.equal(await mutation, false)
  assert.deepEqual(requested, scopeA)
  assert.equal(state.created.value, null)
  assert.deepEqual(state.invitations.value, [])
  assert.equal(state.busy.value, false)
})

test('created links are memory-only and not retained in the invitation list or raw token field', async () => {
  const state = createBusinessTeamState(api())
  await state.setScope(scopeA)
  assert.equal(await state.create({ kind: 'link' }), true)
  assert.equal(state.created.value?.invite_url, '/register?token=fixture-secret')
  assert.equal(state.created.value?.token, undefined)
  assert.equal('invite_url' in state.invitations.value[0]!, false)
  assert.equal('token' in state.invitations.value[0]!, false)
  await state.load(false)
  assert.equal(state.created.value, null)
})

test('revocation only targets loaded pending invitations and authorization loss removes all sensitive state', async () => {
  const source = api()
  let revokes = 0
  source.revoke = async () => { revokes++; throw Object.assign(new Error('denied'), { status: 403 }) }
  const state = createBusinessTeamState(source)
  await state.setScope(scopeA)
  assert.equal(await state.revoke(999), false)
  assert.equal(revokes, 0)
  await state.create({ kind: 'link' })
  assert.equal(await state.revoke(1), false)
  assert.equal(state.error.value, 'denied')
  assert.equal(state.available.value, false)
  assert.equal(state.created.value, null)
  assert.deepEqual(state.members.value, [])
  assert.deepEqual(state.invitations.value, [])
  assert.equal(canRevokeTeamInvitation({ ...invitation(), status: 'revoked' }), false)
  assert.equal(canRevokeTeamInvitation({ ...invitation(), expires_at: '2000-01-01' }), false)
})

test('only same-origin relative registration links with one token can be copied', () => {
  assert.equal(teamInviteUrl('/register?token=fixture', 'https://kb.example'), 'https://kb.example/register?token=fixture')
  for (const value of ['https://evil.example/register?token=x', '//evil.example/register?token=x', 'javascript:alert(1)', '/register?token=x&token=y', '/register?token=x&redirect=https://evil.example', '/register?token=x#secret', '/register?token=']) {
    assert.equal(teamInviteUrl(value, 'https://kb.example'), '')
  }
})

test('transport captures tenant and JWT before dispatch without redirects or response body leakage', async () => {
  let token = 'fixture-token-a'
  let captured: RequestInit | undefined
  const reply = deferred<Response>()
  const request = createTeamTransport({ base: () => '', token: () => token, fetch: async (_url, options) => { captured = options; return reply.promise } })
  const scope = { ...scopeA }
  const pending = request(scope, '/invitations', 'POST', { kind: 'link' })
  token = 'fixture-token-b'
  scope.tenantId = scopeB.tenantId
  reply.resolve(new Response(JSON.stringify({ secret: 'must-not-be-shown' }), { status: 403 }))
  await assert.rejects(pending, (error: Error & { status?: number }) => error.message === 'TEAM_REQUEST_FAILED' && error.status === 403)
  const headers = captured?.headers as Record<string, string>
  assert.equal(headers.Authorization, 'Bearer fixture-token-a')
  assert.equal(headers['X-Tenant-ID'], String(scopeA.tenantId))
  assert.equal(captured?.cache, 'no-store')
  assert.equal(captured?.redirect, 'error')
  assert.equal(captured?.body, '{"kind":"link"}')
})

test('transport refuses empty authentication and out-of-scope endpoint paths before fetch', async () => {
  let calls = 0
  const request = createTeamTransport({ base: () => '', token: () => 'fixture', fetch: async () => { calls++; return new Response('{}') } })
  await assert.rejects(request({ ...scopeA, userId: '' }))
  await assert.rejects(request(scopeA, '/../../system/admin'))
  await assert.rejects(request(scopeA, '/invitations/-1', 'DELETE'))
  assert.equal(calls, 0)
})
