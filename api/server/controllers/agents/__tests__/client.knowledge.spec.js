jest.mock('@librechat/api', () => ({
  ...jest.requireActual('@librechat/api'),
  createKnowledgeStreamCallback: jest.fn(() => ({ name: 'synthetic-knowledge-scheduler' })),
}));

const { createKnowledgeAnswerContext, createKnowledgeStreamCallback } = require('@librechat/api');
const AgentClient = require('../client');

const hit = {
  documentId: 'doc_1',
  revisionId: 'revision_1',
  blockId: 'b1',
  title: '合成课程',
  snippet: '课程共12次。',
  href: '/knowledge/documents/doc_1/revisions/revision_1#block-b1',
  score: 1,
  matchedBy: ['keyword'],
};

function client(parts, items = [hit]) {
  const context = createKnowledgeAnswerContext({
    query: '课程有几次？',
    mode: 'hybrid',
    sourceStatus: 'ready',
    snapshotId: 'snapshot_1',
    items,
  });
  return {
    options: {
      knowledgeAnswer: context,
      req: { knowledgeStreamGuard: { checkNow: jest.fn().mockResolvedValue(undefined) } },
    },
    contentParts: parts,
    chatCompletion: jest.fn().mockResolvedValue(undefined),
    isCompactionTurn: () => false,
    buildResponseMetadata: () => ({ usage: { total_tokens: 20 } }),
  };
}

describe('knowledge completion wiring', () => {
  it.each([
    [],
    [{ type: 'error', error: 'synthetic provider error' }],
    [
      { type: 'text', text: 'unverified partial [1]' },
      { type: 'error', error: 'synthetic' },
    ],
  ])('rejects failed output and clears the shared stream buffer', async (...parts) => {
    const target = client(parts);
    const buffer = target.contentParts;
    await expect(AgentClient.prototype.sendCompletion.call(target, [])).rejects.toMatchObject({
      code: 'KNOWLEDGE_MODEL_UNAVAILABLE',
    });
    expect(buffer).toEqual([]);
  });

  it('replaces seeded output with a deterministic missing-source response without a model call', async () => {
    const target = client([{ type: 'text', text: 'old content' }], []);
    const result = await AgentClient.prototype.sendCompletion.call(target, []);
    expect(target.chatCompletion).not.toHaveBeenCalled();
    expect(result.completion).toEqual([
      { type: 'text', text: target.options.knowledgeAnswer.insufficient },
    ]);
    expect(result.metadata.knowledge.citations).toEqual([]);
  });

  it('publishes mapped citations and preserves native usage only after access validation', async () => {
    const target = client([{ type: 'text', text: '课程共12次。[1]' }]);
    const result = await AgentClient.prototype.sendCompletion.call(target, []);
    expect(target.options.req.knowledgeStreamGuard.checkNow).toHaveBeenCalled();
    expect(result.completion[0].text).toContain(hit.href);
    expect(result.metadata.knowledge.verified).toBe(true);
    expect(result.metadata.usage.total_tokens).toBe(20);
  });

  it('preserves native errors when knowledge mode is disabled', async () => {
    const target = client([{ type: 'error', error: 'synthetic native failure' }]);
    delete target.options.knowledgeAnswer;
    const result = await AgentClient.prototype.sendCompletion.call(target, []);
    expect(result.completion[0].type).toBe('error');
    expect(result.metadata.knowledge).toBeUndefined();
  });
});

describe('knowledge model callback wiring', () => {
  it('preserves the native callbacks without creating a scheduler for ordinary chat', () => {
    const callbacks = [{ name: 'content' }, { name: 'memory' }, { name: 'terminal' }];
    const result = AgentClient.prototype.createModelCallbacks.call({ options: {} }, ...callbacks);

    expect(result).toEqual(callbacks);
    callbacks.forEach((callback, index) => expect(result[index]).toBe(callback));
    expect(createKnowledgeStreamCallback).not.toHaveBeenCalled();
  });

  it('appends an independent knowledge scheduler after all native callbacks for each run', () => {
    const callbacks = [{ name: 'content' }, { name: 'memory' }, { name: 'terminal' }];
    const target = client([]);
    const first = AgentClient.prototype.createModelCallbacks.call(target, ...callbacks);
    const second = AgentClient.prototype.createModelCallbacks.call(target, ...callbacks);

    expect(first).toHaveLength(4);
    callbacks.forEach((callback, index) => {
      expect(first[index]).toBe(callback);
      expect(second[index]).toBe(callback);
    });
    expect(createKnowledgeStreamCallback).toHaveBeenCalledTimes(2);
    expect(first[3]).toBe(createKnowledgeStreamCallback.mock.results[0].value);
    expect(second[3]).toBe(createKnowledgeStreamCallback.mock.results[1].value);
    expect(first[3]).not.toBe(second[3]);
    expect(callbacks).toHaveLength(3);
  });
});
