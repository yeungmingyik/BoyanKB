const mockSearch = jest.fn();
const mockMessages = jest.fn();

jest.mock('@librechat/api', () => ({
  ...jest.requireActual('../../../../packages/api/src/knowledge/answer'),
  resolveKnowledgeConfig: (config) => config,
}));
jest.mock('../Knowledge', () => ({
  getKnowledgeService: async () => ({ search: mockSearch }),
}));
jest.mock('~/models', () => ({ getMessages: (...args) => mockMessages(...args) }));

const { prepareKnowledgeAnswer } = require('../KnowledgeChat');

function request() {
  return {
    config: {
      config: {
        knowledge: {
          enabled: true,
          sync: { enabled: true },
          search: { maxHits: 8, maxResults: 4 },
        },
      },
    },
    user: { id: 'partner-1' },
    body: { text: '课程有几节？', conversationId: 'conversation-1' },
  };
}

describe('knowledge chat preparation', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockSearch.mockImplementation(async ({ query }) => ({
      query,
      mode: 'hybrid',
      sourceStatus: 'ready',
      snapshotId: 'snapshot-1',
      items: [],
    }));
  });

  it('reads the current question from the HTTP body when native runtime metadata omits it', async () => {
    const req = request();
    const context = await prepareKnowledgeAnswer(req, { conversationId: 'conversation-1' });
    expect(mockSearch).toHaveBeenCalledWith({ query: '课程有几节？', mode: 'hybrid', limit: 4 });
    expect(context.query).toBe(req.body.text);
    expect(req.knowledgeContext).toEqual({ snapshotId: 'snapshot-1', items: [] });
    expect(mockMessages).not.toHaveBeenCalled();
  });

  it('limits regenerated questions to the requesting user and conversation', async () => {
    const req = request();
    delete req.body.text;
    mockMessages.mockResolvedValue([{ isCreatedByUser: true, text: '营地有几天？' }]);
    await prepareKnowledgeAnswer(req, {
      conversationId: 'conversation-1',
      parentMessageId: 'message-1',
    });
    expect(mockMessages).toHaveBeenCalledWith(
      { user: 'partner-1', conversationId: 'conversation-1', messageId: 'message-1' },
      'messageId parentMessageId isCreatedByUser text',
      { limit: 1 },
    );
    expect(mockSearch).toHaveBeenCalledWith({ query: '营地有几天？', mode: 'hybrid', limit: 4 });
  });

  it('preserves ordinary LibreChat deployments when knowledge sync is disabled', async () => {
    const req = request();
    req.config.config.knowledge.sync.enabled = false;
    await expect(prepareKnowledgeAnswer(req)).resolves.toBeUndefined();
    expect(mockSearch).not.toHaveBeenCalled();
    expect(req.knowledgeContext).toBeUndefined();
  });

  it('does not generate a context when the source is unavailable', async () => {
    const req = request();
    mockSearch.mockRejectedValue(new Error('KNOWLEDGE_SOURCE_UNAVAILABLE'));
    await expect(prepareKnowledgeAnswer(req)).rejects.toThrow('KNOWLEDGE_SOURCE_UNAVAILABLE');
    expect(req.knowledgeContext).toBeUndefined();
  });
});
