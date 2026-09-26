import { createHash } from 'node:crypto';
import mongoose, { Schema } from 'mongoose';
import { MongoMemoryReplSet } from 'mongodb-memory-server';
import { createKnowledgeModels } from '@librechat/data-schemas';
import type { KnowledgeReadingBlock, TKnowledgeSyncConfig } from 'librechat-data-provider';
import type { FeishuNode, FeishuReader, FeishuBlock } from './feishu';
import type { KnowledgeServiceModels } from './store';
import type { KnowledgeIndexer } from './sync';
import { KnowledgeService } from './sync';
import { KnowledgeError } from './store';
import { FeishuError } from './feishu';

jest.setTimeout(120000);

class MemoryBlobs {
  readonly values = new Map<string, Buffer>();

  async put(bytes: Buffer) {
    const hash = createHash('sha256').update(bytes).digest('hex');
    this.values.set(hash, Buffer.from(bytes));
    return { key: hash, hash, size: bytes.length };
  }

  async read(key: string) {
    const bytes = this.values.get(key);
    if (!bytes) {
      throw new Error('missing');
    }
    return bytes;
  }
}

const config: TKnowledgeSyncConfig = {
  enabled: true,
  wikiUrl: 'https://fixture.feishu.cn/wiki/fixture_entry',
  spaceId: 'space_fixture',
  pollIntervalMs: 600000,
  reconcileIntervalMs: 86400000,
  leaseMs: 120000,
  maxRetries: 0,
  requestTimeoutMs: 30000,
  maxAssetBytes: 52428800,
  maxNodes: 100000,
  maxBlocks: 100000,
  snapshotRetentionDays: 30,
  runRetentionDays: 90,
};

function node(token: string, fields: Partial<FeishuNode> = {}): FeishuNode {
  return {
    space_id: 'space_fixture',
    node_token: token,
    obj_token: `object_${token}`,
    obj_type: 'docx',
    has_child: false,
    title: `Title ${token}`,
    ...fields,
  };
}

function blocks(text = 'Synthetic knowledge', image = false): FeishuBlock[] {
  return [
    {
      block_id: 'page',
      block_type: 1,
      page: { elements: [{ text_run: { content: 'Document title' } }] },
      children: ['paragraph', ...(image ? ['image'] : [])],
    },
    {
      block_id: 'paragraph',
      block_type: 2,
      parent_id: 'page',
      text: { elements: [{ text_run: { content: text } }] },
    },
    ...(image
      ? [
          {
            block_id: 'image',
            block_type: 27,
            parent_id: 'page',
            image: { token: 'private_media' },
          },
        ]
      : []),
  ];
}

describe('knowledge sync with MongoDB transactions', () => {
  let replica: MongoMemoryReplSet;
  let models: KnowledgeServiceModels;
  let blobs: MemoryBlobs;
  let reader: jest.Mocked<FeishuReader>;
  let indexer: KnowledgeIndexer & { index: jest.Mock; verify: jest.Mock };
  let service: KnowledgeService;
  let time: Date;

  beforeAll(async () => {
    replica = await MongoMemoryReplSet.create({
      replSet: { count: 1 },
    });
    await mongoose.connect(replica.getUri('boyankb_sync_test'));
    const Agent = mongoose.model(
      'KnowledgeTestAgent',
      new Schema({ id: String, tool_resources: Schema.Types.Mixed }),
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
    await models.Agent.create({ id: 'agent_fixture' });
    blobs = new MemoryBlobs();
    time = new Date('2026-01-01T00:00:00Z');
    reader = {
      resolveSpace: jest.fn().mockResolvedValue({ spaceId: 'space_fixture', node: node('leaf') }),
      listNodes: jest.fn().mockResolvedValue({ items: [node('a')], hasMore: false }),
      getNode: jest.fn().mockResolvedValue(node('a')),
      getDocument: jest.fn().mockImplementation(async (token) => ({
        document_id: token,
        title: `Title ${token.replace(/^object_/, '')}`,
        revision_id: 1,
      })),
      getBlocks: jest.fn().mockResolvedValue(blocks()),
      getMetadata: jest.fn().mockResolvedValue({ metas: [], failed_list: [] }),
      downloadMedia: jest
        .fn()
        .mockResolvedValue({ buffer: Buffer.from('fixture image'), contentType: 'image/png' }),
    };
    indexer = {
      version: 'fixture-index-1',
      index: jest.fn(async ({ revisionId }) => ({ fileIds: [`file_${revisionId}`] })),
      verify: jest.fn().mockResolvedValue(true),
    };
    service = new KnowledgeService({
      models,
      blobs,
      client: reader,
      indexer,
      config: { agentId: 'agent_fixture', sync: config },
      now: () => time,
      workerId: 'fixture-worker',
    });
    await service.ensureSource();
  });

  async function sync(key: string, mode: 'full' | 'incremental' = 'full') {
    const run = await service.enqueue({ mode, idempotencyKey: key });
    await service.processNext();
    return service.getRun(run.id);
  }

  async function active() {
    return (await models.KnowledgeDocument.findOne({
      sourceId: service.sourceId,
      objToken: 'object_a',
    }).lean())!;
  }

  it('resolves the full space from a leaf and recursively deduplicates objects', async () => {
    reader.listNodes.mockImplementation(async (_space, parent, cursor) => {
      if (parent === 'folder') {
        return {
          items: [node('shortcut', { obj_token: 'object_a', parent_node_token: 'folder' })],
          hasMore: false,
        };
      }
      return cursor
        ? { items: [node('a')], hasMore: false }
        : { items: [node('folder', { has_child: true })], hasMore: true, pageToken: 'next' };
    });
    const run = await sync('recursive');
    expect(run.status).toBe('completed');
    expect(run.counts).toMatchObject({ nodes: 3, documents: 2, published: 2 });
    expect(reader.listNodes).toHaveBeenCalledWith('space_fixture', undefined, undefined);
    expect(indexer.index).toHaveBeenCalledTimes(2);
    const tree = await service.listTree();
    expect(tree.items).toHaveLength(2);
    expect(tree.items.find((item) => item.title === 'Title folder')?.hasChildren).toBe(true);
    const body = await service.readDocument((await active()).id);
    expect(JSON.stringify({ tree, body })).not.toContain('object_a');
    expect(JSON.stringify(body.blocks)).toContain('Synthetic knowledge');
    expect(
      (await models.Agent.findOne({ id: 'agent_fixture' }).lean())?.tool_resources?.file_search
        ?.file_ids,
    ).toEqual(expect.arrayContaining(await service.activeFileIds()));
  });

  it('reuses a stable snapshot and index for title-only changes', async () => {
    await sync('first');
    const first = await active();
    reader.listNodes.mockResolvedValue({
      items: [node('a', { title: 'Directory alias' })],
      hasMore: false,
    });
    reader.getDocument
      .mockResolvedValueOnce({ document_id: 'object_a', title: 'Before rename', revision_id: 2 })
      .mockResolvedValueOnce({ document_id: 'object_a', title: 'Renamed', revision_id: 2 });
    const run = await sync('rename', 'incremental');
    expect(run.counts.unchanged).toBe(1);
    expect(indexer.index).toHaveBeenCalledTimes(1);
    expect((await active()).activeRevisionId).toBe(first.activeRevisionId);
    expect((await service.readDocument(first.id)).title).toBe('Renamed');
    expect((await service.listTree()).items[0].title).toBe('Directory alias');
  });

  it('publishes canonical document titles independently from shortcut labels', async () => {
    reader.listNodes.mockResolvedValue({
      items: [node('a'), node('alias', { obj_token: 'object_a', title: 'Shortcut label' })],
      hasMore: false,
    });
    reader.getDocument.mockResolvedValue({
      document_id: 'object_a',
      title: 'Canonical title',
      revision_id: 1,
    });
    indexer.index.mockImplementationOnce(async ({ revisionId, title }) => {
      expect(title).toBe('Canonical title');
      expect((await active()).title).toBe('Title a');
      expect((await active()).activeRevisionId).toBeUndefined();
      return { fileIds: [`file_${revisionId}`] };
    });
    await sync('canonical');
    expect(indexer.index).toHaveBeenCalledTimes(1);
    expect((await service.readDocument((await active()).id)).title).toBe('Canonical title');
    expect((await service.listTree()).items.map((item) => item.title).sort()).toEqual([
      'Shortcut label',
      'Title a',
    ]);
  });

  it.each(['', '   '])(
    'retains the published title for empty canonical title %p',
    async (title) => {
      await sync('original');
      const original = await active();
      reader.listNodes.mockResolvedValue({
        items: [node('a', { title: 'Changed alias' })],
        hasMore: false,
      });
      reader.getDocument.mockResolvedValue({ document_id: 'object_a', title, revision_id: 2 });
      await sync('empty-title');
      expect((await service.readDocument(original.id)).title).toBe(original.title);
      expect((await active()).activeRevisionId).toBe(original.activeRevisionId);
      expect(indexer.index).toHaveBeenCalledTimes(1);
    },
  );

  it('does not replace a published title when source access fails after discovery', async () => {
    await sync('original');
    const original = await active();
    reader.listNodes.mockResolvedValue({
      items: [node('a', { title: 'Unpublished alias' })],
      hasMore: false,
    });
    reader.getDocument.mockResolvedValue({
      document_id: 'object_a',
      title: 'Unpublished title',
      revision_id: 2,
    });
    reader.getBlocks.mockRejectedValueOnce(new FeishuError('auth', 'source'));
    expect((await sync('source-failure')).status).toBe('failed');
    expect((await active()).title).toBe(original.title);
    expect((await active()).activeRevisionId).toBe(original.activeRevisionId);
  });

  it('persists page cursors and resumes a crashed worker without duplicate publishing', async () => {
    reader.listNodes.mockImplementation(async (_space, _parent, cursor) =>
      cursor
        ? { items: [node('b')], hasMore: false }
        : { items: [node('a')], hasMore: true, pageToken: 'next' },
    );
    const run = await service.enqueue({ mode: 'full', idempotencyKey: 'resume' });
    await service.processNext({ maxSteps: 2 });
    const item = await models.KnowledgeItem.findOne({ runId: run.id, kind: 'folder' }).lean();
    expect(item?.cursor).toBe('next');
    const takeover = new KnowledgeService({
      models,
      blobs,
      client: reader,
      indexer,
      config: { agentId: 'agent_fixture', sync: config },
      workerId: 'replacement',
    });
    await takeover.processNext();
    expect((await service.getRun(run.id)).status).toBe('completed');
    expect(reader.listNodes.mock.calls.filter((call) => call[2] === undefined)).toHaveLength(1);
    expect(await models.KnowledgeRevision.countDocuments({})).toBe(2);
  });

  it('enforces one source lease and rejects stale fencing before any write', async () => {
    const first = (await service.acquire('first', config.leaseMs))!;
    expect(await service.acquire('second', config.leaseMs)).toBeNull();
    await models.KnowledgeSource.updateOne(
      { id: service.sourceId },
      { $set: { leaseUntil: new Date(0) } },
    );
    const second = (await service.acquire('second', config.leaseMs))!;
    expect(second.fence).toBeGreaterThan(first.fence);
    await expect(
      service.mutate(first, async (session) => {
        await models.KnowledgeSource.updateOne(
          { id: service.sourceId },
          { $set: { health: 'paused' } },
          { session },
        );
      }),
    ).rejects.toMatchObject({ code: 'KNOWLEDGE_LEASE_LOST' });
    expect((await service.source()).health).toBe('pending');
    await service.release(first);
    expect((await service.source()).leaseOwner).toBe('second');
    await service.release(second);
  });

  it('coalesces concurrent enqueue idempotency keys', async () => {
    const runs = await Promise.all(
      Array.from({ length: 6 }, () => service.enqueue({ mode: 'full', idempotencyKey: 'same' })),
    );
    expect(new Set(runs.map((run) => run.id)).size).toBe(1);
    expect(await models.KnowledgeRun.countDocuments({})).toBe(1);
  });

  it('does not publish a mixed source revision', async () => {
    reader.getDocument
      .mockResolvedValueOnce({ document_id: 'object_a', title: 'A', revision_id: 1 })
      .mockResolvedValueOnce({ document_id: 'object_a', title: 'A', revision_id: 2 });
    const run = await sync('changing');
    expect(run.status).toBe('partial');
    expect((await active()).activeRevisionId).toBeUndefined();
    expect(indexer.index).not.toHaveBeenCalled();
  });

  it('keeps an old snapshot when extraction or indexing fails', async () => {
    await sync('old');
    const original = await active();
    reader.listNodes.mockResolvedValue({
      items: [node('a', { title: 'Changed alias' })],
      hasMore: false,
    });
    reader.getDocument.mockResolvedValue({
      document_id: 'object_a',
      title: 'Changed title',
      revision_id: 2,
    });
    reader.getBlocks.mockResolvedValue(blocks('Changed body'));
    indexer.index.mockRejectedValue(new Error('fixture failure'));
    const result = await sync('index-failure');
    expect(result.status).toBe('partial');
    expect((await active()).activeRevisionId).toBe(original.activeRevisionId);
    expect((await service.readDocument(original.id)).title).toBe(original.title);
    expect(JSON.stringify((await service.readDocument(original.id)).blocks)).toContain(
      'Synthetic knowledge',
    );
    expect(await service.activeFileIds()).toEqual([`file_${original.activeRevisionId}`]);
  });

  it('keeps unsupported and partial formats visible without publishing empty bodies', async () => {
    reader.listNodes.mockResolvedValue({
      items: [node('a'), node('sheet', { obj_type: 'sheet' })],
      hasMore: false,
    });
    reader.getBlocks.mockResolvedValue([{ block_id: 'unknown', block_type: 999 }]);
    const result = await sync('formats');
    expect(result.status).toBe('partial');
    expect((await service.listTree()).items.map((item) => item.status).sort()).toEqual([
      'partial',
      'unsupported',
    ]);
    expect(await service.activeFileIds()).toEqual([]);
    expect(indexer.index).not.toHaveBeenCalled();
  });

  it('withholds a document and all its assets on confirmed permission loss', async () => {
    reader.getBlocks.mockResolvedValue(blocks('Body with image', true));
    await sync('image');
    const original = await active();
    const document = await service.readDocument(original.id);
    expect(document.assets).toHaveLength(1);
    const image = document.blocks
      .flatMap((block: KnowledgeReadingBlock) => block.children ?? [])
      .find((block: KnowledgeReadingBlock) => block.type === 'image');
    expect(image?.mediaId).toBe(document.assets[0].id);
    expect((await service.readAsset(document.assets[0].id)).bytes.toString()).toBe('fixture image');
    reader.getDocument.mockRejectedValue(new FeishuError('document_denied'));
    await sync('denied');
    await expect(service.readDocument(original.id)).rejects.toMatchObject({ status: 404 });
    await expect(service.readAsset(document.assets[0].id)).rejects.toMatchObject({ status: 404 });
    expect(await service.activeFileIds()).toEqual([]);
    expect(
      (await models.Agent.findOne({ id: 'agent_fixture' }).lean())?.tool_resources?.file_search
        ?.file_ids,
    ).toEqual([]);
  });

  it('pauses the entire source and requires document revalidation after recovery', async () => {
    await sync('healthy');
    const original = await active();
    reader.resolveSpace.mockRejectedValueOnce(new FeishuError('auth', 'source'));
    expect((await sync('source-denied')).status).toBe('failed');
    expect((await service.getSourceHealth()).sourceStatus).toBe('paused');
    await expect(service.readDocument(original.id)).rejects.toMatchObject({ status: 503 });
    expect(
      (await models.Agent.findOne({ id: 'agent_fixture' }).lean())?.tool_resources?.file_search
        ?.file_ids,
    ).toEqual([]);
    await service.enqueue({ mode: 'full', idempotencyKey: 'recover' });
    await service.processNext({ maxSteps: 1 });
    await expect(service.readDocument(original.id)).rejects.toMatchObject({ status: 404 });
    expect((await service.listTree()).items).toEqual([]);
    await service.processNext({ maxSteps: 1 });
    expect((await service.listTree()).items).toEqual([]);
    await service.processNext();
    expect((await service.readDocument(original.id)).revision.id).toBe(original.activeRevisionId);
  });

  it('never deletes missing objects after a failed enumeration page', async () => {
    await sync('healthy');
    const original = await active();
    reader.listNodes
      .mockResolvedValueOnce({ items: [], hasMore: true, pageToken: 'broken' })
      .mockRejectedValueOnce(new FeishuError('transient', 'source'));
    expect(await sync('page-failure')).toMatchObject({
      status: 'failed',
      errorCode: 'FEISHU_TRANSIENT',
    });
    expect(reader.getNode).not.toHaveBeenCalled();
    expect((await service.readDocument(original.id)).id).toBe(original.id);
    expect((await service.source()).lastCompleteScan?.toISOString()).toBe(time.toISOString());
  });

  it('confirms missing nodes before removal and requires two complete scans when still resolvable', async () => {
    await sync('healthy');
    const original = await active();
    reader.listNodes.mockResolvedValue({ items: [], hasMore: false });
    expect((await sync('first-missing')).items).toEqual([]);
    expect((await service.readDocument(original.id)).id).toBe(original.id);
    expect(await models.KnowledgeRun.countDocuments({ status: 'queued' })).toBe(1);
    await service.processNext();
    await expect(service.readDocument(original.id)).rejects.toMatchObject({ status: 404 });
    expect(reader.getNode).toHaveBeenCalledTimes(2);
    expect(await service.activeFileIds()).toEqual([]);
  });

  it('removes a moved node after confirmed out-of-space lookup', async () => {
    await sync('healthy');
    const original = await active();
    reader.listNodes.mockResolvedValue({ items: [], hasMore: false });
    reader.getNode.mockResolvedValue(node('a', { space_id: 'other_space' }));
    await sync('moved');
    await expect(service.readDocument(original.id)).rejects.toMatchObject({ status: 404 });
  });

  it('does not remove a missing node on transient confirmation failures', async () => {
    reader.listNodes.mockResolvedValue({ items: [node('a'), node('b')], hasMore: false });
    await sync('healthy');
    const original = await active();
    reader.listNodes.mockResolvedValue({ items: [], hasMore: false });
    reader.getNode.mockRejectedValue(new FeishuError('transient'));
    const run = await sync('unconfirmed');
    expect(run.status).toBe('partial');
    expect(run.counts.failed).toBe(2);
    expect(run.items).toHaveLength(2);
    expect(run.items.map((item) => item.title).sort()).toEqual(['Title a', 'Title b']);
    for (const item of run.items) {
      expect(item).toMatchObject({ status: 'failed', errorCode: 'FEISHU_TRANSIENT' });
      expect(item.id).toMatch(/^item_[a-f0-9]{64}$/);
      expect(item.documentId).toMatch(/^doc_[a-f0-9]{64}$/);
    }
    const firstPage = await service.getRun(run.id, { limit: 1 });
    expect(firstPage.items).toEqual([run.items[0]]);
    expect(firstPage.nextCursor).toMatch(/^item_[a-f0-9]{64}$/);
    const secondPage = await service.getRun(run.id, { limit: 1, cursor: firstPage.nextCursor });
    expect(secondPage.items).toEqual([run.items[1]]);
    expect(secondPage.nextCursor).toBeUndefined();
    expect(JSON.stringify(run)).not.toMatch(/object_a|object_b|space_fixture|private_media/);
    expect((await service.readDocument(original.id)).id).toBe(original.id);
  });

  it('preserves shared content while another in-space node still references it', async () => {
    reader.listNodes.mockResolvedValue({
      items: [node('a'), node('alias', { obj_token: 'object_a' })],
      hasMore: false,
    });
    await sync('aliases');
    const original = await active();
    reader.listNodes.mockResolvedValue({
      items: [node('alias', { obj_token: 'object_a' })],
      hasMore: false,
    });
    reader.getNode.mockRejectedValue(new FeishuError('not_found'));
    await sync('alias-survives');
    expect((await service.readDocument(original.id)).id).toBe(original.id);
    expect(indexer.index).toHaveBeenCalledTimes(1);
  });

  it('rejects page cycles and excludes cross-space shortcuts', async () => {
    reader.listNodes
      .mockResolvedValueOnce({
        items: [node('foreign', { origin_space_id: 'elsewhere' })],
        hasMore: true,
        pageToken: 'loop',
      })
      .mockResolvedValueOnce({ items: [], hasMore: true, pageToken: 'loop' });
    const run = await sync('page-cycle');
    expect(run.status).toBe('failed');
    expect(run.errorCode).toBe('KNOWLEDGE_PAGE_CYCLE');
    expect(reader.getBlocks).not.toHaveBeenCalled();
    expect((await service.listTree()).items[0]).toMatchObject({
      status: 'unsupported',
      readable: false,
    });
  });

  it('rolls back publication when the configured native Agent disappears', async () => {
    await service.enqueue({ mode: 'full', idempotencyKey: 'agent-missing' });
    await service.processNext({ maxSteps: 2 });
    await models.Agent.deleteMany({});
    const result = await service.processNext();
    expect(result?.status).toBe('partial');
    expect((await active()).activeRevisionId).toBeUndefined();
    expect(await models.KnowledgeRevision.countDocuments({ status: 'published' })).toBe(0);
  });

  it('preserves source identity across different entry URLs and rejects a different space', async () => {
    const replacement = new KnowledgeService({
      models,
      blobs,
      client: reader,
      indexer,
      config: {
        agentId: 'agent_fixture',
        sync: { ...config, wikiUrl: 'https://fixture.feishu.cn/wiki/another_entry' },
      },
    });
    await replacement.ensureSource();
    expect(replacement.sourceId).toBe(service.sourceId);
    const other = new KnowledgeService({
      models,
      blobs,
      client: reader,
      indexer,
      config: { agentId: 'agent_fixture', sync: { ...config, spaceId: 'other' } },
    });
    await expect(other.ensureSource()).rejects.toBeInstanceOf(KnowledgeError);
    expect(await models.KnowledgeSource.countDocuments({})).toBe(1);
  });

  it('schedules the configured poll and full reconciliation intervals', async () => {
    expect((await service.tick())?.mode).toBe('full');
    expect(await service.tick()).toBeNull();
    time = new Date(time.getTime() + config.pollIntervalMs);
    expect((await service.tick())?.mode).toBe('incremental');
    time = new Date(time.getTime() + config.reconcileIntervalMs);
    expect((await service.tick())?.mode).toBe('full');
  });

  it('prevents an expired worker from publishing after an external index call', async () => {
    let started!: () => void;
    let resume!: () => void;
    const entered = new Promise<void>((resolve) => {
      started = resolve;
    });
    const blocked = new Promise<void>((resolve) => {
      resume = resolve;
    });
    indexer.index.mockImplementationOnce(async ({ revisionId }) => {
      started();
      await blocked;
      return { fileIds: [`file_${revisionId}`] };
    });
    await service.enqueue({ mode: 'full', idempotencyKey: 'fenced-publish' });
    const process = service.processNext().catch((error: unknown) => error);
    await entered;
    await models.KnowledgeSource.updateOne(
      { id: service.sourceId },
      { $set: { leaseUntil: new Date(0) } },
    );
    const lease = (await service.acquire('new-worker', config.leaseMs))!;
    resume();
    expect(await process).toMatchObject({ code: 'KNOWLEDGE_LEASE_LOST' });
    expect((await active()).activeRevisionId).toBeUndefined();
    expect(await models.KnowledgeRevision.countDocuments({ status: 'published' })).toBe(0);
    expect(
      (await models.Agent.findOne({ id: 'agent_fixture' }).lean())?.tool_resources?.file_search
        ?.file_ids,
    ).toEqual([]);
    await service.release(lease);
    await service.processNext();
    expect((await active()).status).toBe('published');
  });

  it('rolls back mutations when the lease expires before commit', async () => {
    const lease = (await service.acquire('fixture', config.leaseMs))!;
    await expect(
      service.mutate(lease, async (session) => {
        await models.KnowledgeSource.updateOne(
          { id: service.sourceId },
          { $set: { health: 'paused', leaseUntil: new Date(0) } },
          { session },
        );
      }),
    ).rejects.toMatchObject({ code: 'KNOWLEDGE_LEASE_LOST' });
    expect((await service.source()).health).toBe('pending');
    await service.release(lease);
  });

  it('retains historical run results and immutable source timestamps', async () => {
    reader.listNodes.mockResolvedValue({
      items: [node('a', { obj_edit_time: '1000' })],
      hasMore: false,
    });
    const firstRun = await sync('first-history');
    const first = await active();
    reader.listNodes.mockResolvedValue({
      items: [node('a', { obj_edit_time: '2000' })],
      hasMore: false,
    });
    reader.getBlocks.mockResolvedValue([{ block_id: 'unsupported', block_type: 999 }]);
    const partialRun = await sync('partial-history');
    expect(partialRun.items[0]).toMatchObject({ status: 'partial' });
    expect(partialRun.items[0].missing).toEqual(
      expect.arrayContaining([expect.objectContaining({ blockType: 999 })]),
    );
    expect((await service.getRun(firstRun.id)).items[0].status).toBe('published');
    const old = await service.readDocument(first.id, { revisionId: first.activeRevisionId });
    expect(old.revision.sourceUpdatedAt).toBe(new Date(1000 * 1000).toISOString());
  });

  it('requires verified index files before publication', async () => {
    indexer.verify.mockResolvedValue(false);
    const run = await sync('unverified-index');
    expect(run.items[0].errorCode).toBe('KNOWLEDGE_INDEX_UNAVAILABLE');
    expect((await active()).activeRevisionId).toBeUndefined();
    expect(await service.activeFileIds()).toEqual([]);
  });

  it('moves nodes to root and offlines a replaced object', async () => {
    reader.listNodes.mockImplementation(async (_space, parent) =>
      parent
        ? { items: [node('a', { parent_node_token: 'folder' })], hasMore: false }
        : { items: [node('folder', { has_child: true })], hasMore: false },
    );
    await sync('nested');
    const original = await active();
    reader.listNodes.mockResolvedValue({
      items: [node('a', { obj_token: 'replacement' })],
      hasMore: false,
    });
    reader.getNode.mockRejectedValue(new FeishuError('not_found'));
    await sync('replace');
    expect((await service.listTree()).items).toEqual([
      expect.objectContaining({ title: 'Title a', parentId: null }),
    ]);
    await expect(service.readDocument(original.id)).rejects.toMatchObject({ status: 404 });
  });

  it('does not restore denied documents before a successful new publication', async () => {
    await sync('publish');
    const original = await active();
    reader.getDocument.mockRejectedValueOnce(new FeishuError('document_denied'));
    await sync('deny');
    reader.getBlocks.mockRejectedValueOnce(new FeishuError('transient'));
    await sync('failed-recover');
    await expect(service.readDocument(original.id)).rejects.toMatchObject({ status: 404 });
    expect((await service.listTree()).items).toEqual([]);
    expect(await service.activeFileIds()).toEqual([]);
  });

  it('retries failed full scans on the poll cadence instead of waiting a whole day', async () => {
    reader.resolveSpace.mockRejectedValueOnce(new FeishuError('auth', 'source'));
    expect((await service.tick())?.status).toBe('failed');
    expect(await service.tick()).toBeNull();
    time = new Date(time.getTime() + config.pollIntervalMs);
    expect((await service.tick())?.status).toBe('completed');
    expect((await service.getSourceHealth()).sourceStatus).toBe('ready');
  });
});
