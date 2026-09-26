const express = require('express');
const request = require('supertest');

const mockManager = {
  getJob: jest.fn(),
  subscribe: jest.fn(),
  subscribeWithResume: jest.fn(),
  getResumeState: jest.fn(),
  markSyncSent: jest.fn(),
  abortJob: jest.fn(),
  steering: {
    claimDetailed: jest.fn().mockResolvedValue({ generationProtocolVersion: 1, steers: [] }),
  },
};
const mockAttach = jest.fn();
const mockAssert = jest.fn();
const mockDispose = jest.fn();
const mockCheck = jest.fn();
let mockAllowed;
let mockStop;

jest.mock('@librechat/api', () => ({
  ...jest.requireActual('@librechat/api'),
  ...jest.requireActual('../../../../../packages/api/src/knowledge/streamOutput'),
  GenerationJobManager: mockManager,
  isEnabled: () => false,
}));
jest.mock('~/server/services/KnowledgeStream', () => ({
  attachKnowledgeStream: (...args) => mockAttach(...args),
  assertKnowledgeJobAccess: (...args) => mockAssert(...args),
}));
jest.mock('~/models', () => ({ saveMessage: jest.fn() }));
jest.mock('~/server/middleware', () => ({
  requireJwtAuth: (req, _res, next) => {
    req.user = { id: 'synthetic-user' };
    next();
  },
  configMiddleware: (req, _res, next) => {
    req.config = { config: { knowledge: { enabled: true } } };
    next();
  },
  checkBan: (_req, _res, next) => next(),
  uaParser: (_req, _res, next) => next(),
  moderateText: (_req, _res, next) => next(),
  messageIpLimiter: (_req, _res, next) => next(),
  messageUserLimiter: (_req, _res, next) => next(),
}));
jest.mock('~/server/routes/agents/chat', () => require('express').Router());
jest.mock('~/server/routes/agents/v1', () => ({ v1: require('express').Router() }));
jest.mock('~/server/routes/agents/openai', () => require('express').Router());
jest.mock('~/server/routes/agents/responses', () => require('express').Router());
jest.mock('~/server/routes/agents/management', () => require('express').Router());

const app = express();
app.use(express.json());
app.use('/agents', require('../index'));
const raw = 'synthetic unverified model and source payload';
const verified = {
  final: true,
  responseMessage: {
    text: 'Verified answer',
    content: [{ type: 'text', text: 'Verified answer' }],
    metadata: { knowledge: { verified: true, citations: [] } },
  },
};

function fail(code, status = 403) {
  return Object.assign(new Error(code), { name: 'KnowledgeStreamError', code, status });
}

function frames(response) {
  return response.text
    .split('\n')
    .filter((line) => line.startsWith('data: '))
    .map((line) => JSON.parse(line.slice(6)));
}

describe('knowledge native stream routes', () => {
  beforeEach(() => {
    mockAllowed = true;
    mockCheck.mockResolvedValue(undefined);
    mockAssert.mockResolvedValue(undefined);
    mockAttach.mockImplementation(async ({ onStop }) => {
      mockStop = (error) => {
        mockAllowed = false;
        onStop(error);
      };
      return { checkNow: mockCheck, allowWrite: () => mockAllowed, dispose: mockDispose };
    });
    mockManager.getJob.mockResolvedValue({
      createdAt: 123,
      status: 'running',
      metadata: { userId: 'synthetic-user' },
    });
    mockManager.getResumeState.mockResolvedValue({
      responseMessageId: 'synthetic-response',
      userMessage: { messageId: 'synthetic-question', text: 'Question' },
      aggregatedContent: [raw],
      runSteps: [raw],
    });
  });

  it('withholds model and tool chunks and publishes only the verified final answer', async () => {
    mockManager.subscribe.mockImplementation(async (_id, write, done) => {
      setImmediate(() => {
        write({ event: 'on_message_delta', data: { content: raw } });
        write({ event: 'on_tool_end', data: { output: raw } });
        done(verified);
      });
      return { unsubscribe: jest.fn() };
    });
    const response = await request(app).get('/agents/chat/stream/synthetic-stream');
    expect(response.status).toBe(200);
    expect(response.text).not.toContain(raw);
    expect(frames(response)).toHaveLength(1);
    expect(frames(response)[0].responseMessage.text).toBe('Verified answer');
    expect(mockCheck).toHaveBeenCalledTimes(1);
    expect(mockDispose).toHaveBeenCalledTimes(1);
  });

  it('turns an unverified terminal response into an empty shell', async () => {
    mockManager.subscribe.mockImplementation(async (_id, _write, done) => {
      setImmediate(() => done({ final: true, responseMessage: { text: raw, content: [raw] } }));
      return { unsubscribe: jest.fn() };
    });
    const response = await request(app).get('/agents/chat/stream/synthetic-stream');
    expect(response.text).not.toContain(raw);
    expect(frames(response)[0].responseMessage).toMatchObject({ text: '', content: [] });
  });

  it('hides replay buffers while retaining the response identity needed for reconnection', async () => {
    mockManager.subscribeWithResume.mockImplementation(async (_id, _write, done) => ({
      subscription: { unsubscribe: jest.fn(), activate: () => setImmediate(() => done(verified)) },
      resumeState: await mockManager.getResumeState(),
      pendingEvents: [{ event: 'on_message_delta', data: { content: raw } }],
    }));
    const response = await request(app).get('/agents/chat/stream/synthetic-stream?resume=true');
    expect(response.text).not.toContain(raw);
    expect(frames(response)[0]).toMatchObject({
      sync: true,
      pendingEvents: [],
      resumeState: { responseMessageId: 'synthetic-response', aggregatedContent: [], runSteps: [] },
    });
  });

  it('closes an established stream before emitting its final when the fresh source check fails', async () => {
    mockCheck.mockImplementation(async () => {
      const error = fail('KNOWLEDGE_SOURCE_CHANGED', 503);
      mockStop(error);
      throw error;
    });
    mockManager.subscribe.mockImplementation(async (_id, _write, done) => {
      setImmediate(() => done(verified));
      return { unsubscribe: jest.fn() };
    });
    const response = await request(app).get('/agents/chat/stream/synthetic-stream');
    expect(response.text).not.toContain('Verified answer');
    expect(frames(response)).toEqual([{ error: 'KNOWLEDGE_SOURCE_CHANGED' }]);
  });

  it('rejects a revoked reconnect before SSE headers or replay subscription', async () => {
    mockAttach.mockRejectedValue(fail('KNOWLEDGE_ACCESS_DENIED'));
    const response = await request(app).get('/agents/chat/stream/synthetic-stream?resume=true');
    expect(response.status).toBe(403);
    expect(response.body.error).toBe('KNOWLEDGE_ACCESS_DENIED');
    expect(mockManager.subscribeWithResume).not.toHaveBeenCalled();
  });

  it('freshly authorizes status and excludes aggregated content and run steps', async () => {
    const response = await request(app).get('/agents/chat/status/synthetic-stream');
    expect(response.status).toBe(200);
    expect(response.text).not.toContain(raw);
    expect(response.body).toMatchObject({
      active: true,
      aggregatedContent: [],
      resumeState: { responseMessageId: 'synthetic-response', runSteps: [], aggregatedContent: [] },
    });
    expect(mockAssert).toHaveBeenCalledTimes(1);
  });

  it('returns a static status failure when the source was paused', async () => {
    mockAssert.mockRejectedValue(fail('KNOWLEDGE_SOURCE_UNAVAILABLE', 503));
    const response = await request(app).get('/agents/chat/status/synthetic-stream');
    expect(response.status).toBe(503);
    expect(response.body.error).toBe('KNOWLEDGE_SOURCE_UNAVAILABLE');
    expect(response.text).not.toContain(raw);
  });

  it('removes in-flight content before the native abort persistence and final publication', async () => {
    mockManager.abortJob.mockImplementation(async (_id, options) => {
      expect(options.transformAbortContent([{ type: 'text', text: raw }], {})).toEqual([]);
      return { success: true };
    });
    const response = await request(app)
      .post('/agents/chat/abort')
      .send({ streamId: 'synthetic-stream', generationCreatedAt: 123 });
    expect(response.status).toBe(200);
    expect(response.body.success).toBe(true);
    expect(mockManager.abortJob).toHaveBeenCalledTimes(1);
  });
});
