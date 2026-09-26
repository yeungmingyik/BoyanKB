import { createHash } from 'node:crypto';
import mongoose, { Schema } from 'mongoose';
import { MongoMemoryReplSet } from 'mongodb-memory-server';
import { createKnowledgeModels } from '@librechat/data-schemas';
import type { KnowledgeReadingBlock } from 'librechat-data-provider';
import type { KnowledgeSemanticSearch } from './search';
import type { KnowledgeServiceModels } from './store';
import { KnowledgeStore, knowledgeId } from './store';
import { KnowledgeSearchService } from './search';

jest.setTimeout(120000);

describe('knowledge search over MongoDB published revisions', () => {
  let replica: MongoMemoryReplSet;
  let models: KnowledgeServiceModels;
  let blobs: Map<string, Buffer>;
  let store: KnowledgeStore;
  let semantic: jest.Mocked<KnowledgeSemanticSearch>;
  let search: KnowledgeSearchService;
  const ownerId = 'fixture-owner';
  const sourceId = knowledgeId('source', 'fixture');

  beforeAll(async () => {
    replica = await MongoMemoryReplSet.create({ replSet: { count: 1 } });
    await mongoose.connect(replica.getUri('boyankb_search_test'));
    const Agent = mongoose.model(
      'SearchTestAgent',
      new Schema({ id: String, author: String, tool_resources: Schema.Types.Mixed }),
    );
    models = { ...createKnowledgeModels(mongoose), Agent } as unknown as KnowledgeServiceModels;
    await Promise.all(Object.values(models).map((model) => model.init()));
  });

  afterAll(async () => {
    await mongoose.disconnect();
    await replica?.stop();
  });

  beforeEach(async () => {
    await Promise.all(Object.values(models).map((model) => model.deleteMany({})));
    await models.Agent.create({
      id: 'agent_fixture',
      author: ownerId,
      tool_resources: { file_search: { file_ids: [] } },
    });
    await models.KnowledgeSource.create({
      id: sourceId,
      spaceId: 'private_space',
      agentId: 'agent_fixture',
      enabled: true,
      health: 'healthy',
      accessEpoch: 1,
    });
    blobs = new Map();
    store = new KnowledgeStore(
      models,
      {
        put: async (bytes) => {
          const hash = createHash('sha256').update(bytes).digest('hex');
          blobs.set(hash, bytes);
          return { key: hash, hash, size: bytes.length };
        },
        read: async (key) => blobs.get(key)!,
      },
      sourceId,
    );
    semantic = { query: jest.fn().mockResolvedValue([]) };
    search = new KnowledgeSearchService({ store, semantic, ownerId });
  });

  async function document(name: string, title: string, texts: string[], parentId?: string) {
    const id = knowledgeId('doc', name);
    const nodeId = knowledgeId('node', name);
    const revisionId = knowledgeId('rev', name);
    const fileId = `file_${name}`;
    const blocks: KnowledgeReadingBlock[] = [
      {
        id: 'b1',
        type: 'section',
        text: '',
        children: texts.map((text, index) => ({ id: `b${index + 2}`, type: 'paragraph', text })),
      },
    ];
    const blob = await store.blobs.put(Buffer.from(JSON.stringify({ blocks })));
    await models.KnowledgeDocument.create({
      id,
      sourceId,
      objType: 'docx',
      objToken: `private_${name}`,
      title,
      status: 'published',
      activeRevisionId: revisionId,
      accessEpoch: 1,
    });
    await models.KnowledgeNode.create({
      id: nodeId,
      sourceId,
      nodeToken: `private_${name}`,
      documentId: id,
      title,
      objType: 'docx',
      lastSeenRunId: 'fixture',
      accessEpoch: 1,
      state: 'active',
      parentId,
    });
    await models.KnowledgeRevision.create({
      id: revisionId,
      sourceId,
      documentId: id,
      idempotencyKey: revisionId,
      sourceRevision: '1',
      contentHash: blob.hash,
      blobKey: blob.key,
      text: texts.join('\n'),
      parserVersion: 'fixture',
      indexVersion: 'fixture',
      nativeFileIds: [fileId],
      complete: true,
      status: 'published',
      extractedAt: new Date(),
      publishedAt: new Date(),
      sourceUpdatedAt: new Date('2026-01-01T00:00:00Z'),
    });
    await models.Agent.updateOne(
      { id: 'agent_fixture' },
      { $push: { 'tool_resources.file_search.file_ids': fileId } },
    );
    return { id, nodeId, revisionId, fileId, blobKey: blob.key };
  }

  it('finds Chinese words in natural questions and cites exact published reading blocks', async () => {
    const first = await document('a', '营地课程', [
      '学生参观机器人实验室。',
      '制作纸板机械臂，体验人工智能。',
    ]);
    await document('b', '成人培训', ['办公室效率与电子表格。']);
    const result = await search.search({ query: '如何参加机器人营地？', mode: 'keyword' });
    expect(result.items.length).toBeGreaterThan(0);
    expect(result.items.every((hit) => hit.documentId === first.id)).toBe(true);
    expect(result.items[0]).toMatchObject({
      revisionId: first.revisionId,
      matchedBy: ['keyword'],
      sourceUpdatedAt: '2026-01-01T00:00:00.000Z',
    });
    expect(result.items[0].href).toBe(
      `/knowledge/documents/${first.id}/revisions/${first.revisionId}#block-${result.items[0].blockId}`,
    );
    expect(JSON.stringify(result)).not.toMatch(/private_|file_a|fixture-owner|blobKey/);
    expect(semantic.query).not.toHaveBeenCalled();
    expect(await search.validateHits(result.items, result.snapshotId)).toBe(true);
  });

  it('restricts queries to directory descendants and uses directory names and aliases', async () => {
    const parent = await document('parent', '机器人事业部', ['团队概况']);
    const child = await document('child', '课程', ['学习编程'], parent.nodeId);
    const outside = await document('outside', '机器人', ['外部目录内容']);
    const result = await search.search({
      query: '机器人',
      mode: 'keyword',
      directoryId: parent.nodeId,
    });
    expect(new Set(result.items.map((hit) => hit.documentId))).toEqual(
      new Set([parent.id, child.id]),
    );
    expect(result.items.some((hit) => hit.documentId === outside.id)).toBe(false);
    await expect(
      search.search({ query: '机器人', directoryId: knowledgeId('node', 'missing') }),
    ).rejects.toMatchObject({ code: 'KNOWLEDGE_DIRECTORY_UNAVAILABLE', status: 404 });
  });

  it('combines lexical and semantic matches without duplicate blocks or unrelated filler', async () => {
    const first = await document('a', '机械课程', ['制作机器人并学习传感器。', '另外的段落。']);
    const second = await document('b', '餐饮指南', ['餐饮指南内容。']);
    semantic.query.mockResolvedValue([
      { fileId: first.fileId, text: '制作机器人并学习传感器。', score: 0.8 },
      { fileId: second.fileId, text: '餐饮指南内容。', score: 0.1 },
    ]);
    const result = await search.search({ query: '机器人', mode: 'hybrid' });
    expect(result.items).toHaveLength(1);
    expect(result.items[0]).toMatchObject({
      documentId: first.id,
      blockId: 'b2',
      matchedBy: ['keyword', 'semantic'],
    });
    expect(semantic.query.mock.calls[0][0].fileIds.sort()).toEqual(
      [first.fileId, second.fileId].sort(),
    );
  });

  it('maps semantic chunks spanning blocks only to overlapping snapshot text', async () => {
    const first = await document('a', '课程', ['前段内容', '中段内容', '末段内容']);
    semantic.query.mockResolvedValue([
      { fileId: first.fileId, text: '段内容\n中段内容', score: 0.8 },
    ]);
    const result = await search.search({ query: '活动方法', mode: 'semantic' });
    expect(result.items.map((hit) => hit.blockId).sort()).toEqual(['b2', 'b3']);
  });

  it.each(['foreign-file', 'foreign-content', 'invalid-score'])(
    'fails closed on %s returned by semantic retrieval',
    async (kind) => {
      const first = await document('a', '课程', ['机器人课程']);
      semantic.query.mockResolvedValue([
        {
          fileId: kind === 'foreign-file' ? 'other_file' : first.fileId,
          text: kind === 'foreign-content' ? '不属于当前版本' : '机器人课程',
          score: kind === 'invalid-score' ? NaN : 0.8,
        },
      ]);
      await expect(search.search({ query: '机器人' })).rejects.toMatchObject({
        code: 'KNOWLEDGE_SEARCH_SCOPE_INVALID',
      });
    },
  );

  it('does not fall back to lexical success when semantic retrieval fails', async () => {
    await document('a', '课程', ['机器人课程']);
    semantic.query.mockRejectedValue(new Error('unavailable'));
    await expect(search.search({ query: '机器人' })).rejects.toThrow();
  });

  it('treats regex syntax as literal text and returns no invented matches', async () => {
    await document('a', '课程', ['机器人课程']);
    expect((await search.search({ query: '.*', mode: 'keyword' })).items).toEqual([]);
    expect((await search.search({ query: '无关词语', mode: 'hybrid' })).items).toEqual([]);
  });

  it('restricts the semantic file scope to the selected subtree', async () => {
    const parent = await document('parent', '营地', ['机器人']);
    const child = await document('child', '课程', ['传感器'], parent.nodeId);
    await document('outside', '其他', ['机器人']);
    await search.search({ query: '机器人', mode: 'semantic', directoryId: parent.nodeId });
    expect(semantic.query.mock.calls[0][0].fileIds.sort()).toEqual(
      [parent.fileId, child.fileId].sort(),
    );
  });

  it('does not search unpublished or revalidation-required versions', async () => {
    const first = await document('a', '课程', ['机器人']);
    await models.KnowledgeDocument.updateOne(
      { id: first.id },
      { $set: { requiresRevalidation: true } },
    );
    await models.Agent.updateOne(
      { id: 'agent_fixture' },
      { $set: { 'tool_resources.file_search.file_ids': [] } },
    );
    expect((await search.search({ query: '机器人' })).items).toEqual([]);
    expect(semantic.query).not.toHaveBeenCalled();
  });

  it('fails explicitly when source scanning exceeds the configured document budget', async () => {
    await document('a', '课程', ['机器人']);
    await document('b', '课程', ['无人机']);
    search = new KnowledgeSearchService({ store, semantic, ownerId, config: { maxDocuments: 1 } });
    await expect(search.search({ query: '机器人' })).rejects.toMatchObject({
      code: 'KNOWLEDGE_SEARCH_BUDGET_EXCEEDED',
    });
  });

  it('validates minimal citation projections and rejects nonexistent block anchors', async () => {
    await document('a', '课程', ['机器人']);
    const result = await search.search({ query: '机器人', mode: 'keyword' });
    const references = result.items.map(({ documentId, revisionId, blockId }) => ({
      documentId,
      revisionId,
      blockId,
    }));
    expect(await search.validateHits(references, result.snapshotId)).toBe(true);
    expect(
      await search.validateHits([{ ...references[0], blockId: 'b999' }], result.snapshotId),
    ).toBe(false);
  });

  it('keeps the published snapshot searchable while an update is partial', async () => {
    const first = await document('a', '课程', ['机器人旧版本']);
    await models.KnowledgeDocument.updateOne({ id: first.id }, { $set: { status: 'partial' } });
    const result = await search.search({ query: '旧版本', mode: 'keyword' });
    expect(result.items[0].revisionId).toBe(first.revisionId);
  });

  it.each(['pause', 'epoch', 'revision', 'removed'])(
    'rejects %s changed during a query and invalidates streaming references',
    async (change) => {
      const first = await document('a', '课程', ['机器人课程']);
      const before = await search.search({ query: '机器人', mode: 'keyword' });
      semantic.query.mockImplementation(async () => {
        if (change === 'pause')
          await models.KnowledgeSource.updateOne({ id: sourceId }, { $set: { health: 'paused' } });
        if (change === 'epoch')
          await models.KnowledgeSource.updateOne({ id: sourceId }, { $inc: { accessEpoch: 1 } });
        if (change === 'revision')
          await models.KnowledgeDocument.updateOne(
            { id: first.id },
            { $set: { activeRevisionId: knowledgeId('rev', 'new') } },
          );
        if (change === 'removed') {
          await models.KnowledgeDocument.updateOne(
            { id: first.id },
            { $set: { status: 'removed' } },
          );
          await models.Agent.updateOne(
            { id: 'agent_fixture' },
            { $set: { 'tool_resources.file_search.file_ids': [] } },
          );
        }
        return [{ fileId: first.fileId, text: '机器人课程', score: 0.8 }];
      });
      await expect(search.search({ query: '机器人' })).rejects.toThrow();
      expect(await search.validateHits(before.items, before.snapshotId).catch(() => false)).toBe(
        false,
      );
    },
  );

  it('invalidates empty retrieval contexts when the source epoch changes', async () => {
    const empty = await search.search({ query: '没有相关内容' });
    expect(empty.items).toEqual([]);
    expect(await search.validateHits([], empty.snapshotId)).toBe(true);
    await models.KnowledgeSource.updateOne({ id: sourceId }, { $inc: { accessEpoch: 1 } });
    expect(await search.validateHits([], empty.snapshotId)).toBe(false);
  });

  it('paginates a stable snapshot and rejects cursors after publication changes', async () => {
    const first = await document('a', '课程', ['机器人一', '机器人二', '机器人三']);
    const page = await search.search({ query: '机器人', mode: 'keyword', limit: 1 });
    const next = await search.search({
      query: '机器人',
      mode: 'keyword',
      limit: 1,
      cursor: page.nextCursor,
    });
    expect(page.items[0].blockId).not.toBe(next.items[0].blockId);
    await models.KnowledgeDocument.updateOne({ id: first.id }, { $set: { title: '新标题' } });
    await expect(
      search.search({ query: '机器人', mode: 'keyword', cursor: next.nextCursor }),
    ).rejects.toMatchObject({ code: 'KNOWLEDGE_SEARCH_SNAPSHOT_CHANGED', status: 409 });
  });

  it('bounds returned snippets and total context without losing remaining pagination', async () => {
    await document(
      'a',
      '课程',
      Array.from({ length: 6 }, () => '机器人'.repeat(200)),
    );
    search = new KnowledgeSearchService({
      store,
      semantic,
      ownerId,
      config: { maxContextChars: 512, maxSnippetChars: 256 },
    });
    const result = await search.search({ query: '机器人', mode: 'keyword' });
    expect(result.items.reduce((sum, hit) => sum + hit.snippet.length, 0)).toBe(512);
    expect(result.items).toHaveLength(2);
    expect(result.nextCursor).toBeDefined();
  });

  it('rejects stale native mapping, wrong ownership and malformed snapshot text', async () => {
    const first = await document('a', '课程', ['机器人课程']);
    await models.Agent.updateOne(
      { id: 'agent_fixture' },
      { $push: { 'tool_resources.file_search.file_ids': 'unpublished' } },
    );
    await expect(search.search({ query: '机器人', mode: 'keyword' })).rejects.toMatchObject({
      code: 'KNOWLEDGE_SEARCH_SCOPE_INVALID',
    });
    await models.Agent.updateOne(
      { id: 'agent_fixture' },
      { $set: { author: 'other', 'tool_resources.file_search.file_ids': [first.fileId] } },
    );
    await expect(search.search({ query: '机器人' })).rejects.toMatchObject({
      code: 'KNOWLEDGE_SEARCH_SCOPE_INVALID',
    });
    await models.Agent.updateOne({ id: 'agent_fixture' }, { $set: { author: ownerId } });
    blobs.set(
      first.blobKey,
      Buffer.from(JSON.stringify({ blocks: [{ id: 'b1', type: 'paragraph', text: 'different' }] })),
    );
    await expect(search.search({ query: '机器人' })).rejects.toMatchObject({
      code: 'KNOWLEDGE_SEARCH_SNAPSHOT_INVALID',
    });
  });

  it.each([
    { query: '' },
    { query: 'x'.repeat(1001) },
    { query: 'x', limit: 100 },
    { query: 'x', file_ids: ['other'] },
    { query: 'x', directoryId: 'https://source.invalid' },
    { query: 'x', cursor: '!!' },
  ])('rejects invalid input %j', async (input) => {
    await expect(search.search(input)).rejects.toMatchObject({
      code: 'KNOWLEDGE_SEARCH_QUERY_INVALID',
      status: 400,
    });
  });
});
