import express from 'express';
import request from 'supertest';
import type { RequestHandler } from 'express';
import type { KnowledgeReadService } from './routes';
import { createKnowledgeAccess } from './access';
import { createKnowledgeRouter } from './routes';
import { KnowledgeError } from './store';

const documentId = `doc_${'a'.repeat(64)}`;
const revisionId = `revision_${'b'.repeat(64)}`;
const assetId = `asset_${'c'.repeat(64)}`;
const runId = '12345678-1234-1234-1234-123456789abc';
const counts = {
  nodes: 0,
  documents: 0,
  published: 0,
  unchanged: 0,
  failed: 0,
  unsupported: 0,
  inaccessible: 0,
  removed: 0,
  media: 0,
};

function fixture() {
  const viewers = new Set(['partner']);
  const banned = new Set<string>();
  const service: jest.Mocked<KnowledgeReadService> = {
    listTree: jest.fn().mockResolvedValue({ items: [], sourceStatus: 'ready' }),
    readDocument: jest.fn().mockResolvedValue({
      id: documentId,
      title: 'Synthetic document',
      status: 'published',
      blocks: [],
      assets: [],
    }),
    readAsset: jest.fn().mockResolvedValue({
      bytes: Buffer.from('0123456789'),
      size: 10,
      contentType: 'image/png',
      name: 'Synthetic.png',
    }),
    listRuns: jest.fn().mockResolvedValue({ items: [], sourceStatus: 'ready' }),
    getRun: jest.fn().mockResolvedValue({
      id: runId,
      mode: 'full',
      status: 'completed',
      phase: 'complete',
      counts,
      items: [],
    }),
    enqueue: jest
      .fn()
      .mockResolvedValue({ id: runId, mode: 'full', status: 'queued', phase: 'queued', counts }),
    activeFileIds: jest.fn().mockResolvedValue([]),
  };
  const getService = jest.fn(async () => service);
  const access = createKnowledgeAccess({
    getConfig: async () => ({ enabled: true, agentId: 'agent_knowledge' }),
    getAgent: async () => ({ _id: { toString: () => 'agent_resource' }, id: 'agent_knowledge' }),
    hasUserPermission: async (userId) => viewers.has(userId),
    deleteUserSessions: async () => {},
  });
  const authenticate: RequestHandler = (req, res, next) => {
    const userId = req.get('Authorization');
    if (!userId) {
      res.status(401).json({ code: 'AUTH_REQUIRED' });
      return;
    }
    req.user = { id: userId, role: userId === 'admin' ? 'ADMIN' : 'USER' } as Express.User;
    next();
  };
  const checkBan: RequestHandler = (req, res, next) => {
    if (banned.has((req.user as { id: string }).id)) {
      res.status(403).json({ code: 'USER_BANNED' });
      return;
    }
    next();
  };
  const app = express();
  app.use(express.json());
  app.use('/api', access.createBoundary(authenticate, checkBan));
  app.use('/api/knowledge', createKnowledgeRouter({ getService, authorize: access.authorize }));
  return { app, service, getService, viewers, banned };
}

describe('knowledge routes', () => {
  it.each([
    '/tree',
    `/documents/${documentId}`,
    `/documents/${documentId}/revisions/${revisionId}`,
    `/assets/${assetId}`,
    '/sync-runs',
  ])('rejects anonymous access to %s before obtaining the read service', async (path) => {
    const { app, getService } = fixture();
    expect((await request(app).get(`/api/knowledge${path}`)).status).toBe(401);
    expect(getService).not.toHaveBeenCalled();
  });

  it.each(['/tree', `/documents/${documentId}`, `/assets/${assetId}`])(
    'requires a live VIEW grant at %s',
    async (path) => {
      const { app, service, viewers, getService } = fixture();
      expect(
        (await request(app).get(`/api/knowledge${path}`).set('Authorization', 'denied')).status,
      ).toBe(403);
      expect(getService).not.toHaveBeenCalled();
      expect(
        (await request(app).get(`/api/knowledge${path}`).set('Authorization', 'partner')).status,
      ).toBe(200);
      viewers.delete('partner');
      expect(
        (await request(app).get(`/api/knowledge${path}`).set('Authorization', 'partner')).status,
      ).toBe(403);
      expect(
        service.listTree.mock.calls.length +
          service.readDocument.mock.calls.length +
          service.readAsset.mock.calls.length,
      ).toBe(1);
    },
  );

  it('checks bans for read and administration requests even with a VIEW grant or ADMIN role', async () => {
    const { app, banned, getService } = fixture();
    banned.add('partner');
    banned.add('admin');
    expect(
      (
        await request(app)
          .get(`/api/knowledge/documents/${documentId}`)
          .set('Authorization', 'partner')
      ).status,
    ).toBe(403);
    expect(
      (
        await request(app)
          .post('/api/knowledge/sync-runs')
          .set('Authorization', 'admin')
          .set('Idempotency-Key', 'request_1')
          .send({ mode: 'full' })
      ).status,
    ).toBe(403);
    expect(getService).not.toHaveBeenCalled();
  });

  it.each(['/sync-runs', `/sync-runs/${runId}`])(
    'rejects partner access to administrative history %s',
    async (path) => {
      const { app, getService } = fixture();
      expect(
        (await request(app).get(`/api/knowledge${path}`).set('Authorization', 'partner')).status,
      ).toBe(403);
      expect(getService).not.toHaveBeenCalled();
    },
  );

  it('rejects partner sync and retry requests before enqueueing work', async () => {
    const { app, service } = fixture();
    for (const path of ['/sync-runs', `/sync-runs/${runId}/retry`]) {
      expect(
        (
          await request(app)
            .post(`/api/knowledge${path}`)
            .set('Authorization', 'partner')
            .set('Idempotency-Key', 'request_1')
            .send({ mode: 'full' })
        ).status,
      ).toBe(403);
    }
    expect(service.enqueue).not.toHaveBeenCalled();
  });

  it('passes validated pagination and internal revision IDs without exposing private source fields', async () => {
    const { app, service } = fixture();
    const tree = await request(app)
      .get(`/api/knowledge/tree?limit=25&cursor=${documentId}&parentId=${documentId}`)
      .set('Authorization', 'partner');
    expect(tree.status).toBe(200);
    expect(service.listTree).toHaveBeenCalledWith({
      cursor: documentId,
      parentId: documentId,
      limit: 25,
    });
    expect(tree.headers['cache-control']).toBe('private, no-store');
    expect(tree.headers['x-content-type-options']).toBe('nosniff');
    expect(
      (
        await request(app)
          .get(`/api/knowledge/documents/${documentId}/revisions/${revisionId}`)
          .set('Authorization', 'partner')
      ).status,
    ).toBe(200);
    expect(service.readDocument).toHaveBeenCalledWith(documentId, { revisionId });
  });

  it.each([
    ['/tree?limit=0', 400],
    ['/tree?limit=201', 400],
    ['/tree?limit=1.5', 400],
    ['/tree?limit=bad', 400],
    ['/tree?cursor=private_source_token', 404],
    ['/tree?parentId=private_source_token', 404],
    ['/documents/private_source_token', 404],
    [`/documents/${documentId}/revisions/private_source_token`, 404],
    ['/assets/private_source_token', 404],
  ])('rejects invalid resource and pagination parameters %s', async (path, status) => {
    const { app, service } = fixture();
    expect(
      (await request(app).get(`/api/knowledge${path}`).set('Authorization', 'partner')).status,
    ).toBe(status);
    expect(service.listTree).not.toHaveBeenCalled();
    expect(service.readDocument).not.toHaveBeenCalled();
    expect(service.readAsset).not.toHaveBeenCalled();
  });

  it.each([
    ['bytes=2-5', '2345', 'bytes 2-5/10'],
    ['bytes=7-', '789', 'bytes 7-9/10'],
    ['bytes=-3', '789', 'bytes 7-9/10'],
    ['bytes=8-99', '89', 'bytes 8-9/10'],
  ])('serves valid bounded asset ranges %s', async (range, body, contentRange) => {
    const { app } = fixture();
    const result = await request(app)
      .get(`/api/knowledge/assets/${assetId}`)
      .set('Authorization', 'partner')
      .set('Range', range);
    expect(result.status).toBe(206);
    expect(result.headers['content-range']).toBe(contentRange);
    expect(result.headers['content-length']).toBe(String(body.length));
    expect(result.body.toString()).toBe(body);
    expect(result.headers['accept-ranges']).toBe('bytes');
    expect(result.headers['content-disposition']).toMatch(/^inline;/);
  });

  it.each([
    'bytes=10-',
    'bytes=5-2',
    'bytes=-0',
    'bytes=',
    'items=1-2',
    'bytes=1-2,4-5',
    'bytes=999999999999999999999-',
  ])('returns 416 for invalid range %s', async (range) => {
    const { app } = fixture();
    const result = await request(app)
      .get(`/api/knowledge/assets/${assetId}`)
      .set('Authorization', 'partner')
      .set('Range', range);
    expect(result.status).toBe(416);
    expect(result.headers['content-range']).toBe('bytes */10');
    expect(result.body).toEqual({ code: 'KNOWLEDGE_RANGE_INVALID' });
  });

  it('serves HEAD with the same protected metadata and no body', async () => {
    const { app } = fixture();
    const result = await request(app)
      .head(`/api/knowledge/assets/${assetId}`)
      .set('Authorization', 'partner');
    expect(result.status).toBe(200);
    expect(result.headers['content-length']).toBe('10');
    expect(result.text).toBeUndefined();
  });

  it('forces SVG and other active content to download with a safe encoded filename', async () => {
    const { app, service } = fixture();
    service.readAsset.mockResolvedValue({
      bytes: Buffer.from('<svg/>'),
      size: 6,
      contentType: 'image/svg+xml',
      name: '文档\r\nX-Injected: yes.svg',
    });
    const result = await request(app)
      .get(`/api/knowledge/assets/${assetId}`)
      .set('Authorization', 'partner');
    expect(result.status).toBe(200);
    expect(result.headers['content-type']).toBe('application/octet-stream');
    expect(result.headers['content-disposition']).toMatch(/^attachment;/);
    expect(result.headers['content-disposition']).not.toMatch(/%0D|%0A/i);
    expect(result.headers['x-injected']).toBeUndefined();
    expect(result.headers['content-security-policy']).toBe("default-src 'none'; sandbox");
  });

  it('reports only stable service error codes', async () => {
    const { app, service } = fixture();
    service.readDocument.mockRejectedValueOnce(
      new Error('private_secret source_token document text'),
    );
    const unexpected = await request(app)
      .get(`/api/knowledge/documents/${documentId}`)
      .set('Authorization', 'partner');
    expect(unexpected.status).toBe(503);
    expect(unexpected.body).toEqual({ code: 'KNOWLEDGE_UNAVAILABLE' });
    service.readDocument.mockRejectedValueOnce(new KnowledgeError('KNOWLEDGE_NOT_FOUND', 404));
    const missing = await request(app)
      .get(`/api/knowledge/documents/${documentId}`)
      .set('Authorization', 'partner');
    expect(missing.status).toBe(404);
    expect(missing.body).toEqual({ code: 'KNOWLEDGE_NOT_FOUND' });
  });

  it.each([undefined, 'short', 'contains space', 'a'.repeat(129)])(
    'requires a bounded idempotency key %s',
    async (key) => {
      const { app, service } = fixture();
      const pending = request(app)
        .post('/api/knowledge/sync-runs')
        .set('Authorization', 'admin')
        .send({ mode: 'full' });
      if (key) {
        pending.set('Idempotency-Key', key);
      }
      expect((await pending).status).toBe(400);
      expect(service.enqueue).not.toHaveBeenCalled();
    },
  );

  it('passes the same idempotency key and acting administrator to enqueue and validates the mode', async () => {
    const { app, service } = fixture();
    const invalid = await request(app)
      .post('/api/knowledge/sync-runs')
      .set('Authorization', 'admin')
      .set('Idempotency-Key', 'request_1')
      .send({ mode: 'delete_all' });
    expect(invalid.status).toBe(400);
    expect(service.enqueue).not.toHaveBeenCalled();
    const result = await request(app)
      .post('/api/knowledge/sync-runs')
      .set('Authorization', 'admin')
      .set('Idempotency-Key', 'request_1')
      .send({ mode: 'incremental', actorId: 'spoofed_user' });
    expect(result.status).toBe(202);
    expect(service.enqueue).toHaveBeenCalledWith({
      mode: 'incremental',
      idempotencyKey: 'request_1',
      actorId: 'admin',
    });
    const retried = await request(app)
      .post(`/api/knowledge/sync-runs/${runId}/retry`)
      .set('Authorization', 'admin')
      .set('Idempotency-Key', 'retry_run_1')
      .send({ mode: 'incremental' });
    expect(retried.status).toBe(202);
    expect(service.enqueue).toHaveBeenLastCalledWith({
      mode: 'full',
      idempotencyKey: 'retry_run_1',
      actorId: 'admin',
      retryRunId: runId,
    });
  });
});
