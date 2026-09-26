const mockPermission = jest.fn();
const mockBanGet = jest.fn();
const mockUserRead = jest.fn();
const mockAgentRead = jest.fn();
const mockHealth = jest.fn();
const mockValidateHits = jest.fn();
const mockManager = {
  getJob: jest.fn(),
  updateMetadata: jest.fn(),
  abortJob: jest.fn(),
};

jest.mock('@librechat/api', () => ({
  ...jest.requireActual('../../../../packages/api/src/knowledge/stream'),
  ...jest.requireActual('../../../../packages/api/src/knowledge/streamAccess'),
  resolveKnowledgeConfig: (config) => config,
  createKnowledgeUserPermissionChecker: (check) => check,
  GenerationJobManager: mockManager,
}));
jest.mock('mongoose', () => {
  const query = (read) => ({
    select: () => ({ maxTimeMS: () => ({ lean: () => read() }) }),
  });
  return {
    models: {
      User: { findById: () => query(mockUserRead) },
      Agent: { findOne: () => query(mockAgentRead) },
    },
  };
});
jest.mock('~/models', () => ({ hasPermission: (...args) => mockPermission(...args) }));
jest.mock('~/cache', () => ({ getLogStores: () => ({ get: (...args) => mockBanGet(...args) }) }));
jest.mock('../Knowledge', () => ({
  getKnowledgeService: async () => ({
    getSourceHealth: mockHealth,
    validateHits: mockValidateHits,
  }),
}));

const {
  startKnowledgeGeneration,
  captureKnowledgeContext,
  finishKnowledgeGeneration,
  attachKnowledgeStream,
  assertKnowledgeJobAccess,
} = require('../KnowledgeStream');
const records = [];
const streams = [];
let sequence = 0;

function fixture() {
  const streamId = `synthetic-stream-${++sequence}`;
  const req = {
    config: { config: { knowledge: { enabled: true, agentId: 'synthetic-agent' } } },
    user: { id: 'synthetic-user', tenantId: 'synthetic-tenant' },
    ip: '127.0.0.2',
  };
  const job = {
    createdAt: sequence,
    abortController: new AbortController(),
    metadata: { userId: req.user.id },
  };
  return { req, job, streamId };
}

async function start(input) {
  const record = await startKnowledgeGeneration(input);
  records.push(record);
  return record;
}

async function attach(input) {
  const stream = await attachKnowledgeStream(input);
  streams.push(stream);
  return stream;
}

describe('knowledge generation wiring', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    mockPermission.mockResolvedValue(true);
    mockBanGet.mockResolvedValue(undefined);
    mockUserRead.mockResolvedValue({ role: 'USER', tenantId: 'synthetic-tenant' });
    mockAgentRead.mockResolvedValue({ _id: { toString: () => 'synthetic-agent-resource' } });
    mockHealth.mockResolvedValue({ sourceStatus: 'ready' });
    mockValidateHits.mockResolvedValue(true);
    mockManager.getJob.mockResolvedValue(undefined);
    mockManager.updateMetadata.mockResolvedValue(undefined);
    mockManager.abortJob.mockResolvedValue(undefined);
  });

  afterEach(async () => {
    for (const stream of streams.splice(0)) {
      stream?.dispose();
    }
    for (const record of records.splice(0)) {
      finishKnowledgeGeneration(record);
    }
    await jest.advanceTimersByTimeAsync(60000);
    jest.useRealTimers();
  });

  it('leaves disabled deployments on their existing path', async () => {
    const input = fixture();
    input.req.config.config.knowledge.enabled = false;
    expect(await start(input)).toBeNull();
    expect(await attach(input)).toBeNull();
    await assertKnowledgeJobAccess(input.req, input.job, input.streamId);
    expect(mockUserRead).not.toHaveBeenCalled();
    expect(mockHealth).not.toHaveBeenCalled();
  });

  it('checks source health before search and persists only snapshot identity after search', async () => {
    const input = fixture();
    const record = await start(input);
    expect(mockValidateHits).not.toHaveBeenCalled();
    input.req.knowledgeContext = {
      snapshotId: 'synthetic-snapshot',
      items: [
        { documentId: 'doc', revisionId: 'rev', blockId: 'block', snippet: 'synthetic body' },
      ],
    };
    await captureKnowledgeContext(input.req, record, input.streamId, input.job.createdAt);
    expect(mockValidateHits).toHaveBeenCalledWith(
      [{ documentId: 'doc', revisionId: 'rev', blockId: 'block' }],
      'synthetic-snapshot',
    );
    expect(JSON.stringify(mockManager.updateMetadata.mock.calls)).not.toContain('synthetic body');
  });

  it('freshly reads VIEW on every interval and aborts the exact provider generation', async () => {
    const input = fixture();
    const record = await start(input);
    mockPermission.mockResolvedValue(false);
    await jest.advanceTimersByTimeAsync(1000);
    expect(mockUserRead).toHaveBeenCalledTimes(2);
    expect(record.guard.reason.code).toBe('KNOWLEDGE_ACCESS_DENIED');
    expect(input.job.abortController.signal.aborted).toBe(true);
    expect(mockManager.abortJob).toHaveBeenCalledWith(input.streamId, {
      expectedCreatedAt: input.job.createdAt,
      transformAbortContent: expect.any(Function),
    });
    expect(mockManager.abortJob.mock.calls[0][1].transformAbortContent(['synthetic body'])).toEqual(
      [],
    );
  });

  it.each(['synthetic-user', '127.0.0.2'])(
    'stops administrator generation and established subscribers after a fresh ban on %s',
    async (bannedKey) => {
      mockUserRead.mockResolvedValue({ role: 'ADMIN', tenantId: 'synthetic-tenant' });
      const input = fixture();
      await start(input);
      const onStop = jest.fn();
      const subscriber = await attach({ ...input, onStop });
      mockBanGet.mockImplementation(async (key) =>
        key === bannedKey ? { expiresAt: Date.now() + 60000 } : undefined,
      );
      await jest.advanceTimersByTimeAsync(1000);
      expect(subscriber.reason.code).toBe('KNOWLEDGE_USER_BANNED');
      expect(subscriber.allowWrite()).toBe(false);
      expect(onStop).toHaveBeenCalledTimes(1);
      expect(input.job.abortController.signal.aborted).toBe(true);
      expect(mockPermission).not.toHaveBeenCalled();
    },
  );

  it('revalidates an empty-hit snapshot and stops generation when it changes', async () => {
    const input = fixture();
    input.req.knowledgeContext = { snapshotId: 'synthetic-empty', items: [] };
    const record = await start(input);
    mockValidateHits.mockResolvedValue(false);
    await jest.advanceTimersByTimeAsync(1000);
    expect(mockValidateHits).toHaveBeenCalledWith([], 'synthetic-empty');
    expect(record.guard.reason.code).toBe('KNOWLEDGE_SOURCE_CHANGED');
  });

  it('rejects reconnect and status when access has already been revoked', async () => {
    const input = fixture();
    mockPermission.mockResolvedValue(false);
    await expect(attach(input)).rejects.toMatchObject({ code: 'KNOWLEDGE_ACCESS_DENIED' });
    await expect(
      assertKnowledgeJobAccess(input.req, input.job, input.streamId),
    ).rejects.toMatchObject({
      code: 'KNOWLEDGE_ACCESS_DENIED',
    });
  });

  it('fences a remote replacement and never validates its content as the old turn', async () => {
    const input = fixture();
    mockManager.getJob.mockResolvedValue({ ...input.job, createdAt: input.job.createdAt + 1 });
    await expect(attach(input)).rejects.toMatchObject({ code: 'KNOWLEDGE_ACCESS_DENIED' });
    expect(mockValidateHits).not.toHaveBeenCalled();
  });

  it('revalidates final content after the generation has drained and the job was removed', async () => {
    const input = fixture();
    input.req.knowledgeContext = { snapshotId: 'synthetic-final', items: [] };
    const record = await start(input);
    const subscriber = await attach(input);
    finishKnowledgeGeneration(record);
    await subscriber.checkNow();
    mockHealth.mockResolvedValue({ sourceStatus: 'paused' });
    await expect(subscriber.checkNow()).rejects.toMatchObject({
      code: 'KNOWLEDGE_SOURCE_UNAVAILABLE',
    });
    expect(subscriber.allowWrite()).toBe(false);
  });

  it('fails closed with a static code when source validation throws', async () => {
    const input = fixture();
    mockHealth.mockRejectedValue(new Error('synthetic credential and body'));
    const error = await start(input).catch((value) => value);
    expect(error.code).toBe('KNOWLEDGE_SOURCE_UNAVAILABLE');
    expect(JSON.stringify(error)).not.toContain('synthetic credential');
    expect(JSON.stringify(mockManager.updateMetadata.mock.calls)).not.toContain(
      'synthetic credential',
    );
  });
});
