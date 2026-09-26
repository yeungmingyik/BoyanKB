import express from 'express';
import request from 'supertest';
import { createNativeKnowledgeAccess } from './nativeAccess';
import { createKnowledgeAccess } from './access';
import { KnowledgeError } from './store';

function fixture(enabled = true) {
  const activeFileIds = jest.fn(async () => ['active_file']);
  const getFile = jest.fn(async (id: string) => ({ file_id: id, context: 'knowledge' }));
  const access = createNativeKnowledgeAccess({ activeFileIds, getFile });
  const hasUserPermission = jest.fn(async () => true);
  const knowledge = createKnowledgeAccess({
    getConfig: async () => ({ enabled, agentId: 'agent_fixture' }),
    getAgent: async () => ({ id: 'agent_fixture', _id: 'agent_resource' }),
    hasUserPermission,
    deleteUserSessions: async () => undefined,
  });
  const reached = jest.fn();
  const app = express();
  app.use((req, _res, next) => {
    req.user = { id: 'partner', role: 'USER' } as Express.User;
    next();
  });
  app.use('/api', knowledge.authorize);
  app.use('/api/files', access.guard);
  app.get(
    [
      '/api/files/download/:userId/:file_id',
      '/api/files/download-url/:userId/:file_id',
      '/api/files/:file_id/preview',
      '/api/files/config',
    ],
    (_req, res) => {
      reached();
      res.json({ available: true });
    },
  );
  return { app, activeFileIds, getFile, reached, hasUserPermission, ...access };
}

describe('native knowledge file access', () => {
  it.each([
    '/api/files/DoWnLoAd/vectordb/:id',
    '/api/files/DoWnLoAd-UrL/vectordb/:id',
    '/api/files/:id/PrEvIeW/',
  ])('enforces publication and VIEW for case-insensitive native route %s', async (path) => {
    const { app, getFile, reached, hasUserPermission } = fixture();
    const inactive = await request(app).get(path.replace(':id', 'old_file'));
    expect(inactive.status).toBe(404);
    expect(inactive.body).toEqual({ code: 'KNOWLEDGE_FILE_UNAVAILABLE' });
    expect(getFile).toHaveBeenCalledWith('old_file');
    expect(reached).not.toHaveBeenCalled();
    expect((await request(app).get(path.replace(':id', 'active_file'))).status).toBe(200);
    expect(reached).toHaveBeenCalledTimes(1);
    hasUserPermission.mockResolvedValue(false);
    const revoked = await request(app).get(path.replace(':id', 'active_file'));
    expect(revoked.status).toBe(403);
    expect(revoked.body).toEqual({ code: 'KNOWLEDGE_ACCESS_DENIED' });
    expect(getFile).toHaveBeenCalledTimes(2);
    expect(reached).toHaveBeenCalledTimes(1);
  });

  it.each([
    '/download/vectordb/active_file',
    '/download-url/vectordb/active_file',
    '/active_file/preview',
    '/active_file/preview/',
  ])('allows currently published knowledge file %s', async (path) => {
    const { app, getFile, reached } = fixture();
    expect((await request(app).get(`/api/files${path}`)).status).toBe(200);
    expect(getFile).toHaveBeenCalledWith('active_file');
    expect(reached).toHaveBeenCalledTimes(1);
  });

  it.each(['/download/vectordb/old_file', '/download-url/vectordb/old_file', '/old_file/preview'])(
    'rejects a previous knowledge file at %s',
    async (path) => {
      const { app, reached } = fixture();
      const result = await request(app).get(`/api/files${path}`);
      expect(result.status).toBe(404);
      expect(result.body).toEqual({ code: 'KNOWLEDGE_FILE_UNAVAILABLE' });
      expect(reached).not.toHaveBeenCalled();
    },
  );

  it('rechecks active file membership on every request', async () => {
    const { app, activeFileIds, reached } = fixture();
    expect((await request(app).get('/api/files/active_file/preview')).status).toBe(200);
    activeFileIds.mockResolvedValue([]);
    expect((await request(app).get('/api/files/active_file/preview')).status).toBe(404);
    expect(reached).toHaveBeenCalledTimes(1);
  });

  it('fails closed with a static 404 when the source or native file lookup fails', async () => {
    const source = fixture();
    source.activeFileIds.mockRejectedValue(new Error('private_source_token private_app_secret'));
    const first = await request(source.app).get('/api/files/active_file/preview');
    expect(first.status).toBe(404);
    expect(first.body).toEqual({ code: 'KNOWLEDGE_FILE_UNAVAILABLE' });
    expect(source.reached).not.toHaveBeenCalled();
    const native = fixture();
    native.getFile.mockRejectedValue(new Error('private_database_value'));
    const second = await request(native.app).get('/api/files/download/vectordb/active_file');
    expect(second.status).toBe(404);
    expect(second.body).toEqual({ code: 'KNOWLEDGE_FILE_UNAVAILABLE' });
    expect(native.reached).not.toHaveBeenCalled();
  });

  it('preserves native handling for non-knowledge files', async () => {
    const { app, getFile, activeFileIds, reached } = fixture();
    getFile.mockResolvedValue({ file_id: 'personal_file', context: 'message' });
    expect((await request(app).get('/api/files/personal_file/preview')).status).toBe(200);
    expect(activeFileIds).not.toHaveBeenCalled();
    expect(reached).toHaveBeenCalledTimes(1);
  });

  it('leaves unrelated routes and disabled knowledge mode unchanged', async () => {
    const unrelated = fixture();
    expect((await request(unrelated.app).get('/api/files/config')).status).toBe(200);
    expect(unrelated.getFile).not.toHaveBeenCalled();
    const disabled = fixture(false);
    expect((await request(disabled.app).get('/api/files/old_file/preview')).status).toBe(200);
    expect(disabled.getFile).not.toHaveBeenCalled();
  });

  it('rejects a malformed encoded file ID without passing an exception to native handlers', async () => {
    const { app, reached } = fixture();
    const result = await request(app).get('/api/files/bad%ZZ/preview');
    expect(result.status).toBe(404);
    expect(result.body).toEqual({ code: 'KNOWLEDGE_FILE_UNAVAILABLE' });
    expect(reached).not.toHaveBeenCalled();
  });

  it('filters only inactive knowledge files and preserves unrelated metadata', async () => {
    const { filter, activeFileIds } = fixture();
    const files = [
      { file_id: 'active_file', context: 'knowledge', filename: 'Current.txt' },
      { file_id: 'old_file', context: 'knowledge', filename: 'Old.txt' },
      { file_id: 'personal_file', context: 'message', filename: 'Personal.txt' },
      { file_id: 'other_file', filename: 'Other.txt' },
      { context: 'knowledge', filename: 'Malformed.txt' },
    ];
    expect(await filter(files)).toEqual([files[0], files[2], files[3]]);
    expect(activeFileIds).toHaveBeenCalledTimes(1);
  });

  it('does not query knowledge state for lists that contain no knowledge files', async () => {
    const { filter, activeFileIds } = fixture();
    const files = [{ file_id: 'personal_file', context: 'message' }];
    expect(await filter(files)).toBe(files);
    expect(activeFileIds).not.toHaveBeenCalled();
  });

  it('sanitizes list filtering failures before upstream handlers can log them', async () => {
    const { filter, activeFileIds } = fixture();
    activeFileIds.mockRejectedValue(new Error('private_source_token private_app_secret'));
    const error = await filter([{ file_id: 'active_file', context: 'knowledge' }]).catch(
      (value: unknown) => value,
    );
    expect(error).toBeInstanceOf(KnowledgeError);
    expect(error).toMatchObject({ status: 503 });
    expect(String(error)).not.toMatch(/private_source_token|private_app_secret/);
    expect(JSON.stringify(error)).not.toMatch(/private_source_token|private_app_secret/);
  });
});
