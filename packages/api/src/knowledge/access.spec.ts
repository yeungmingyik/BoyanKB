import express from 'express';
import request from 'supertest';
import { Types } from 'mongoose';
import {
  AccessRoleIds,
  PermissionBits,
  PrincipalType,
  ResourceType,
} from 'librechat-data-provider';
import type { TKnowledgeConfig } from 'librechat-data-provider';
import type { RequestHandler } from 'express';
import {
  createKnowledgeAccess,
  createKnowledgeImageAuthorizer,
  createKnowledgeUserPermissionChecker,
  resolveKnowledgeConfig,
} from './access';

const agentId = 'agent_knowledge';
const resourceId = '65cfb246f7ecadb8b1e8036d';

function fixture() {
  let config: TKnowledgeConfig | undefined = { enabled: true, agentId };
  const granted = new Set(['partner-a', 'partner-b']);
  const banned = new Set<string>();
  const deps = {
    getConfig: jest.fn(async () => config),
    getAgent: jest.fn(async () => ({ _id: resourceId, id: agentId, tools: ['file_search'] })),
    hasUserPermission: jest.fn(async (id: string) => granted.has(id)),
    deleteUserSessions: jest.fn().mockResolvedValue({ deletedCount: 1 }),
  };
  const access = createKnowledgeAccess(deps);
  const authenticate: RequestHandler = (req, res, next) => {
    const id = req.get('authorization');
    if (!id) {
      res.sendStatus(401);
      return;
    }
    req.user = { id, role: id === 'admin' ? 'ADMIN' : 'USER' } as Express.User;
    next();
  };
  const checkBan = jest.fn<ReturnType<RequestHandler>, Parameters<RequestHandler>>(
    (req, res, next) => {
      if (banned.has((req.user as { id: string }).id)) {
        res.sendStatus(403);
        return;
      }
      next();
    },
  );
  const app = express();
  app.use(express.json());
  app.use('/api', access.createBoundary(authenticate, checkBan));
  app.use((req, res) => {
    res.json({
      ok: true,
      filter: res.locals.knowledgeAgentFilter,
      knowledgeEnabled: res.locals.knowledgeEnabled,
    });
  });
  return {
    app,
    access,
    deps,
    granted,
    banned,
    checkBan,
    setConfig: (value: typeof config) => {
      config = value;
    },
  };
}

describe('knowledge access boundary', () => {
  it('resolves the configured Agent environment variable and allows an empty initial value', () => {
    const config = { enabled: true, agentId: '${BOYANKB_KNOWLEDGE_AGENT_ID}' };
    expect(resolveKnowledgeConfig(config, { BOYANKB_KNOWLEDGE_AGENT_ID: agentId })).toEqual({
      enabled: true,
      agentId,
    });
    expect(resolveKnowledgeConfig(config, {})).toEqual({ enabled: true, agentId: '' });
    expect(resolveKnowledgeConfig({ enabled: true, agentId }, {})).toEqual({
      enabled: true,
      agentId,
    });
    expect(resolveKnowledgeConfig(undefined, {})).toBeUndefined();
  });

  it.each([
    '/api/convos',
    '/api/messages',
    '/api/files',
    '/api/agents/chat',
    '/api/share/link',
    '/api/knowledge/access',
    '/api/search',
  ])('rejects anonymous %s', async (path) => {
    const { app } = fixture();
    expect((await request(app).get(path)).status).toBe(401);
  });

  it.each(['/api/convos', '/api/messages', '/api/files', '/api/agents', '/api/search'])(
    'rejects an ungranted account at %s',
    async (path) => {
      const { app } = fixture();
      const result = await request(app).get(path).set('Authorization', 'ungranted');
      expect(result.status).toBe(403);
      expect(result.body.code).toBe('KNOWLEDGE_ACCESS_DENIED');
    },
  );

  it('reports authorization without rejecting the account session', async () => {
    const { app } = fixture();
    const result = await request(app)
      .get('/api/knowledge/access')
      .set('Authorization', 'ungranted');
    expect(result.status).toBe(200);
    expect(result.body).toEqual({ access: false, configured: true, agentId });
    expect((await request(app).get('/api/user').set('Authorization', 'ungranted')).status).toBe(
      200,
    );
  });

  it('preserves native behavior when disabled', async () => {
    const { app, deps, setConfig, checkBan } = fixture();
    setConfig(undefined);
    const response = await request(app).post('/api/agents');
    expect(response.status).toBe(200);
    expect(response.body).not.toHaveProperty('knowledgeEnabled');
    expect(deps.getAgent).not.toHaveBeenCalled();
    expect(checkBan).not.toHaveBeenCalled();
  });

  it.each(['admin', 'partner-a'])(
    'marks knowledge requests before downstream config loading for %s',
    async (userId) => {
      const { app } = fixture();
      const response = await request(app).post('/api/convos/update').set('Authorization', userId);
      expect(response.status).toBe(200);
      expect(response.body.knowledgeEnabled).toBe(true);
    },
  );

  it('keeps authentication and startup endpoints available', async () => {
    const { app, deps } = fixture();
    for (const path of [
      '/api/auth/login',
      '/api/auth/register',
      '/api/auth/refresh',
      '/api/config',
    ]) {
      expect((await request(app).post(path)).status).toBe(200);
    }
    expect(deps.hasUserPermission).not.toHaveBeenCalled();
  });

  it('checks the ban before authorization on history reads', async () => {
    const { app, banned, deps } = fixture();
    banned.add('partner-a');
    expect((await request(app).get('/api/convos').set('Authorization', 'partner-a')).status).toBe(
      403,
    );
    expect(deps.hasUserPermission).not.toHaveBeenCalled();
  });

  it('invalidates old access tokens on the next request after removing VIEW', async () => {
    const { app, granted, access, deps } = fixture();
    const read = () => request(app).get('/api/convos').set('Authorization', 'partner-a');
    expect((await read()).status).toBe(200);
    granted.delete('partner-a');
    await access.revokeSessions({
      resourceType: ResourceType.AGENT,
      resourceId,
      revoked: [{ type: PrincipalType.USER, id: 'partner-a' }],
    });
    expect((await read()).status).toBe(403);
    expect(deps.deleteUserSessions).toHaveBeenCalledWith('partner-a');
    expect((await request(app).get('/api/convos').set('Authorization', 'partner-b')).status).toBe(
      200,
    );
  });

  it('allows ADMIN initialization before the Agent is configured', async () => {
    const { app, setConfig } = fixture();
    setConfig({ enabled: true, agentId: '' });
    expect((await request(app).post('/api/agents').set('Authorization', 'admin')).status).toBe(200);
    expect(
      (await request(app).get('/api/knowledge/access').set('Authorization', 'admin')).body,
    ).toEqual({ access: true, configured: false, agentId: '' });
    expect((await request(app).get('/api/convos').set('Authorization', 'partner-a')).status).toBe(
      503,
    );
  });

  it.each([
    ['post', '/api/agents'],
    ['put', `/api/permissions/agent/${resourceId}`],
    ['post', '/api/files'],
    ['post', '/api/files/images'],
    ['post', '/api/agents/v1/responses'],
    ['post', '/api/agents/v1/chat/completions'],
    ['post', '/api/share'],
    ['get', '/api/share/public-link'],
    ['post', '/api/schedules'],
    ['post', '/api/api-keys'],
    ['post', '/api/convos/import'],
    ['get', '/api/admin/users'],
    ['get', '/api/agents/other-agent'],
    ['post', '/api/agents/chat/resume'],
  ])('blocks partner %s %s', async (method, path) => {
    const { app } = fixture();
    const response = await request(app)[method as 'get'](path).set('Authorization', 'partner-a');
    expect(response.status).toBe(403);
  });

  it('also disables public sharing for administrators', async () => {
    const { app } = fixture();
    expect((await request(app).post('/api/share').set('Authorization', 'admin')).status).toBe(403);
  });

  it.each([
    '/API/SHARE/public-link/',
    '/api/%73hare/public-link',
    '/api/agents%2fv1/responses',
    '/api/agents//v1/responses',
    '/api/files%2fimages',
  ])('rejects alternate path spelling %s', async (path) => {
    const { app } = fixture();
    expect((await request(app).post(path).set('Authorization', 'partner-a')).status).toBe(403);
  });

  it('checks VIEW on case-insensitive routes with a trailing slash', async () => {
    const { app, granted } = fixture();
    expect((await request(app).get('/API/CONVOS/').set('Authorization', 'partner-a')).status).toBe(
      200,
    );
    granted.delete('partner-a');
    expect((await request(app).get('/API/CONVOS/').set('Authorization', 'partner-a')).status).toBe(
      403,
    );
  });

  it('limits the native Agent listing filter', async () => {
    const { app } = fixture();
    const response = await request(app).get('/api/agents').set('Authorization', 'partner-a');
    expect(response.body.filter).toEqual({ id: agentId });
  });

  it.each(['DeepSeek', 'openAI', 'anthropic'])(
    'allows %s chat after VIEW and native personal key management',
    async (endpoint) => {
      const { app } = fixture();
      const response = await request(app)
        .post(`/api/agents/chat/${endpoint}`)
        .set('Authorization', 'partner-a')
        .send({
          endpoint,
          model: 'test-model',
          ephemeralAgent: { mcp: [], web_search: false },
          tools: [],
          codeWorkspaces: [],
        });
      expect(response.status).toBe(200);
      expect(
        (
          await request(app)
            .put('/api/keys')
            .set('Authorization', 'partner-a')
            .send({ name: endpoint, value: 'test-only' })
        ).status,
      ).toBe(200);
    },
  );

  it.each([
    { endpoint: 'agents', agent_id: 'other-agent' },
    { endpoint: 'DeepSeek', ephemeralAgent: { web_search: true } },
    { endpoint: 'DeepSeek', ephemeralAgent: { mcp: ['private-service'] } },
    { endpoint: 'DeepSeek', manualSkills: ['skill-1'] },
    { endpoint: 'DeepSeek', files: [{ file_id: 'other-file' }] },
    { endpoint: 'DeepSeek', tools: ['execute_code'] },
    { endpoint: 'DeepSeek', web_search: true },
    { endpoint: 'DeepSeek', url_context: true },
    { endpoint: 'DeepSeek', execute_code: true },
    { endpoint: 'DeepSeek', file_search: true },
    { endpoint: 'DeepSeek', file_ids: ['other-file'] },
    { endpoint: 'DeepSeek', assistant_id: 'other-assistant' },
    { endpoint: 'DeepSeek', addedConvo: { agent_id: 'other-agent', endpoint: 'agents' } },
    { endpoint: 'DeepSeek', codeWorkspaces: ['workspace'] },
  ])('rejects chat tool or saved Agent overrides %j', async (body) => {
    const { app } = fixture();
    expect(
      (
        await request(app)
          .post('/api/agents/chat/DeepSeek')
          .set('Authorization', 'partner-a')
          .send(body)
      ).status,
    ).toBe(403);
  });

  it.each([
    [true, 503],
    [false, 200],
    [undefined, 200],
  ])('checks saved Agent URL Context %s with status %i', async (urlContext, status) => {
    const { app, deps } = fixture();
    const agent = {
      _id: resourceId,
      id: agentId,
      tools: [],
      model_parameters: { model: 'gemini-2.5-flash', url_context: urlContext },
    };
    deps.getAgent.mockResolvedValue(agent);
    const response = await request(app)
      .post('/api/agents/chat')
      .set('Authorization', 'partner-a')
      .send({ endpoint: 'agents', agent_id: agentId });
    expect(response.status).toBe(status);
    if (urlContext) {
      expect(response.body.code).toBe('KNOWLEDGE_AGENT_UNSAFE');
    } else {
      expect(response.body.ok).toBe(true);
    }
  });

  it('rejects unsafe server-side knowledge Agent tools', async () => {
    const { app, deps } = fixture();
    deps.getAgent.mockResolvedValue({ _id: resourceId, id: agentId, tools: ['web_search'] });
    const response = await request(app)
      .post('/api/agents/chat/agents')
      .set('Authorization', 'partner-a')
      .send({ endpoint: 'agents', agent_id: agentId });
    expect(response.status).toBe(503);
    expect(response.body.code).toBe('KNOWLEDGE_AGENT_UNSAFE');
  });

  it('accepts native individual viewer grants and rejects inherited or editor grants', async () => {
    const { access } = fixture();
    expect(
      await access.validatePermissionUpdate({
        resourceType: 'agent',
        resourceId,
        updated: [{ type: 'user', id: 'partner-a', accessRoleId: AccessRoleIds.AGENT_VIEWER }],
      }),
    ).toBeUndefined();
    for (const updated of [
      [{ type: 'role', id: 'USER', accessRoleId: AccessRoleIds.AGENT_VIEWER }],
      [{ type: 'user', id: 'partner-a', accessRoleId: AccessRoleIds.AGENT_EDITOR }],
      [{ type: 'group', id: 'partners', accessRoleId: AccessRoleIds.AGENT_VIEWER }],
    ]) {
      expect(
        await access.validatePermissionUpdate({ resourceType: 'agent', resourceId, updated }),
      ).toBe('KNOWLEDGE_INDIVIDUAL_VIEW_REQUIRED');
    }
    expect(
      await access.validatePermissionUpdate({ resourceType: 'agent', resourceId, public: true }),
    ).toBe('KNOWLEDGE_INDIVIDUAL_VIEW_REQUIRED');
    expect(
      await access.validatePermissionUpdate({
        resourceType: 'agent',
        resourceId: resourceId.toUpperCase(),
        public: true,
      }),
    ).toBe('KNOWLEDGE_INDIVIDUAL_VIEW_REQUIRED');
  });

  it('only clears refresh sessions for removed users of the configured Agent', async () => {
    const { access, deps } = fixture();
    await access.revokeSessions({
      resourceType: 'agent',
      resourceId: 'another-resource',
      revoked: [{ type: 'user', id: 'partner-a' }],
    });
    expect(deps.deleteUserSessions).not.toHaveBeenCalled();
    await access.revokeSessions({
      resourceType: 'agent',
      resourceId,
      revoked: [
        { type: 'user', id: 'partner-a' },
        { type: 'user', id: 'partner-a' },
        { type: 'role', id: 'USER' },
      ],
    });
    expect(deps.deleteUserSessions).toHaveBeenCalledTimes(1);
  });

  it('queries only the native user principal with ObjectId values', async () => {
    const hasPermission = jest.fn().mockResolvedValue(true);
    const check = createKnowledgeUserPermissionChecker(hasPermission);
    const userId = '65cfb246f7ecadb8b1e8036b';
    expect(await check(userId, resourceId)).toBe(true);
    expect(hasPermission).toHaveBeenCalledWith(
      [{ principalType: PrincipalType.USER, principalId: new Types.ObjectId(userId) }],
      ResourceType.AGENT,
      new Types.ObjectId(resourceId),
      PermissionBits.VIEW,
    );
  });

  it('checks a cookie-authenticated image viewer against ban and VIEW', async () => {
    const req = {
      baseUrl: '/images',
      path: '/owner/file.png',
      method: 'GET',
    } as unknown as express.Request;
    const res = {
      setHeader: jest.fn(),
      status: jest.fn().mockReturnThis(),
      json: jest.fn(),
      locals: {},
    } as unknown as express.Response;
    const { access, granted } = fixture();
    const checkBan: RequestHandler = (_req, _res, next) => next();
    const authorize = createKnowledgeImageAuthorizer({
      getUser: async () => ({ id: 'partner-a', role: 'USER' }) as Express.User,
      checkBan,
      authorize: access.authorize,
    });
    expect(await authorize('partner-a', req, res)).toBe(true);
    granted.delete('partner-a');
    expect(await authorize('partner-a', req, res)).toBe(false);
    expect(res.status).toHaveBeenCalledWith(403);
  });
});
