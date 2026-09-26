import { createHash, randomBytes } from 'node:crypto';
import type {
  KnowledgeTreeResponse,
  KnowledgeDocumentResponse,
  KnowledgeSyncRun,
  KnowledgeSyncCounts,
  KnowledgeSyncRunsResponse,
  KnowledgeSyncRunResponse,
  KnowledgeSourceStatus,
  KnowledgeReadingBlock,
  KnowledgeDocumentStatus,
} from 'librechat-data-provider';
import type {
  KnowledgeModels,
  KnowledgeSourceRecord,
  KnowledgeRunRecord,
  KnowledgeItemRecord,
  KnowledgeRevisionRecord,
  KnowledgeDocumentRecord,
  IAgent,
} from '@librechat/data-schemas';
import type { ClientSession, FilterQuery, Model } from 'mongoose';
import type { KnowledgeBlobStore } from './blob';

export class KnowledgeError extends Error {
  constructor(
    readonly code: string,
    readonly status: number = 503,
  ) {
    super(code);
  }
}

export interface KnowledgeLease {
  sourceId: string;
  owner: string;
  fence: number;
}

export type KnowledgeServiceModels = KnowledgeModels & { Agent: Model<IAgent> };

export interface KnowledgePage {
  cursor?: string;
  limit?: number;
}

export interface KnowledgeSnapshot {
  blocks: KnowledgeReadingBlock[];
}

export interface KnowledgeAssetContent {
  bytes: Buffer;
  contentType: string;
  name: string;
  size: number;
}

export function knowledgeId(kind: string, ...values: string[]): string {
  return `${kind}_${createHash('sha256').update(JSON.stringify(values)).digest('hex')}`;
}

export function knowledgeRunId(): string {
  return `run_${Date.now().toString(16).padStart(12, '0')}${randomBytes(26).toString('hex')}`;
}

export function emptyKnowledgeCounts(): KnowledgeSyncCounts {
  return {
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
}

function sourceStatus(source: KnowledgeSourceRecord): KnowledgeSourceStatus {
  if (!source.enabled || source.health === 'paused' || source.health === 'disabled') {
    return 'paused';
  }
  if (source.health === 'healthy') {
    return 'ready';
  }
  return source.errorCode ? 'error' : 'pending';
}

function pageLimit(page: KnowledgePage): number {
  if (page.cursor && !/^(?:[a-z]+_[a-f0-9]{64}|[a-f0-9-]{36})$/.test(page.cursor)) {
    throw new KnowledgeError('KNOWLEDGE_CURSOR_INVALID', 400);
  }
  const limit = page.limit ?? 100;
  if (!Number.isInteger(limit) || limit < 1 || limit > 200) {
    throw new KnowledgeError('KNOWLEDGE_PAGE_INVALID', 400);
  }
  return limit;
}

function publicRun(run: KnowledgeRunRecord): KnowledgeSyncRun {
  return {
    id: run.id,
    mode: run.mode,
    status: run.status === 'succeeded' ? 'completed' : run.status,
    phase: run.phase,
    counts: { ...emptyKnowledgeCounts(), ...run.counts },
    startedAt: run.startedAt?.toISOString(),
    finishedAt: run.finishedAt?.toISOString(),
    errorCode: run.errorCode,
  };
}

function itemDocumentStatus(item: KnowledgeItemRecord): KnowledgeDocumentStatus {
  if (item.status === 'succeeded') {
    return 'published';
  }
  if (
    item.errorCode === 'KNOWLEDGE_FORMAT_UNSUPPORTED' ||
    item.errorCode === 'KNOWLEDGE_OUT_OF_SCOPE'
  ) {
    return 'unsupported';
  }
  if (item.errorCode === 'FEISHU_DOCUMENT_DENIED') {
    return 'inaccessible';
  }
  if (item.errorCode === 'FEISHU_NOT_FOUND') {
    return 'removed';
  }
  if (item.status === 'partial' || item.status === 'failed') {
    return item.status;
  }
  return item.status === 'running' ? 'extracting' : 'pending';
}

export class KnowledgeStore {
  constructor(
    readonly models: KnowledgeServiceModels,
    readonly blobs: KnowledgeBlobStore,
    public sourceId: string,
  ) {}

  async initialize(): Promise<void> {
    await Promise.all(
      Object.entries(this.models)
        .filter(([name]) => name.startsWith('Knowledge'))
        .map(([, model]) => model.init()),
    );
  }

  async ensureSource(input: {
    agentId: string;
    spaceId?: string;
    enabled: boolean;
  }): Promise<KnowledgeSourceRecord> {
    const current = await this.models.KnowledgeSource.findOne({ id: this.sourceId }).lean();
    if (current?.spaceId && input.spaceId && current.spaceId !== input.spaceId) {
      throw new KnowledgeError('KNOWLEDGE_SOURCE_MISMATCH', 409);
    }
    return (await this.models.KnowledgeSource.findOneAndUpdate(
      { id: this.sourceId },
      {
        $set: { agentId: input.agentId, enabled: input.enabled },
        $setOnInsert: { id: this.sourceId, spaceId: input.spaceId ?? '', health: 'pending' },
      },
      { upsert: true, new: true },
    ).lean())!;
  }

  async source(requireAvailable = false): Promise<KnowledgeSourceRecord> {
    const source = await this.models.KnowledgeSource.findOne({ id: this.sourceId }).lean();
    if (!source) {
      throw new KnowledgeError('KNOWLEDGE_SOURCE_UNCONFIGURED');
    }
    if (requireAvailable && (!source.enabled || source.health !== 'healthy')) {
      throw new KnowledgeError('KNOWLEDGE_SOURCE_UNAVAILABLE');
    }
    return source;
  }

  async acquire(owner: string, leaseMs: number): Promise<KnowledgeLease | null> {
    const source = await this.models.KnowledgeSource.findOneAndUpdate(
      { id: this.sourceId, enabled: true, $expr: { $lte: ['$leaseUntil', '$$NOW'] } },
      [
        {
          $set: {
            leaseOwner: owner,
            leaseFence: { $add: ['$leaseFence', 1] },
            leaseUntil: { $add: ['$$NOW', leaseMs] },
          },
        },
      ],
      { new: true },
    ).lean();
    return source ? { sourceId: this.sourceId, owner, fence: source.leaseFence } : null;
  }

  private leaseFilter(lease: KnowledgeLease): FilterQuery<KnowledgeSourceRecord> {
    return {
      id: this.sourceId,
      enabled: true,
      leaseOwner: lease.owner,
      leaseFence: lease.fence,
      $expr: { $gt: ['$leaseUntil', '$$NOW'] },
    };
  }

  async renew(lease: KnowledgeLease, leaseMs: number): Promise<void> {
    const result = await this.models.KnowledgeSource.updateOne(this.leaseFilter(lease), [
      { $set: { leaseUntil: { $add: ['$$NOW', leaseMs] } } },
    ]);
    if (result.matchedCount !== 1) {
      throw new KnowledgeError('KNOWLEDGE_LEASE_LOST', 409);
    }
  }

  async release(lease: KnowledgeLease): Promise<void> {
    await this.models.KnowledgeSource.updateOne(
      { id: this.sourceId, leaseOwner: lease.owner, leaseFence: lease.fence },
      { $set: { leaseUntil: new Date(0) }, $unset: { leaseOwner: 1 } },
    );
  }

  async mutate<T>(lease: KnowledgeLease, fn: (session: ClientSession) => Promise<T>): Promise<T> {
    const session = await this.models.KnowledgeSource.db.startSession();
    try {
      let value: T | undefined;
      await session.withTransaction(
        async () => {
          const guard = await this.models.KnowledgeSource.updateOne(
            this.leaseFilter(lease),
            { $inc: { writeSequence: 1 } },
            { session },
          );
          if (guard.matchedCount !== 1) {
            throw new KnowledgeError('KNOWLEDGE_LEASE_LOST', 409);
          }
          value = await fn(session);
          const finalGuard = await this.models.KnowledgeSource.updateOne(
            this.leaseFilter(lease),
            { $inc: { writeSequence: 1 } },
            { session },
          );
          if (finalGuard.matchedCount !== 1) {
            throw new KnowledgeError('KNOWLEDGE_LEASE_LOST', 409);
          }
        },
        { readConcern: { level: 'snapshot' }, writeConcern: { w: 'majority' } },
      );
      return value as T;
    } finally {
      await session.endSession();
    }
  }

  async enqueue(input: {
    mode: 'full' | 'incremental';
    idempotencyKey: string;
    actorId?: string;
    retryRunId?: string;
  }): Promise<KnowledgeSyncRun> {
    await this.source();
    if (!/^[A-Za-z0-9_.:-]{1,128}$/.test(input.idempotencyKey)) {
      throw new KnowledgeError('KNOWLEDGE_IDEMPOTENCY_KEY_INVALID', 400);
    }
    if (
      input.retryRunId &&
      !(await this.models.KnowledgeRun.exists({
        sourceId: this.sourceId,
        id: input.retryRunId,
        status: { $in: ['partial', 'failed'] },
      }))
    ) {
      throw new KnowledgeError('KNOWLEDGE_RUN_NOT_RETRYABLE', 409);
    }
    const query = { sourceId: this.sourceId, idempotencyKey: input.idempotencyKey };
    try {
      const run = await this.models.KnowledgeRun.findOneAndUpdate(
        query,
        {
          $setOnInsert: {
            ...input,
            sourceId: this.sourceId,
            id: knowledgeRunId(),
            counts: emptyKnowledgeCounts(),
          },
        },
        { upsert: true, new: true },
      ).lean();
      return publicRun(run!);
    } catch (error) {
      if ((error as { code?: number }).code !== 11000) {
        throw error;
      }
      return publicRun((await this.models.KnowledgeRun.findOne(query).lean())!);
    }
  }

  async listTree(
    page: KnowledgePage & { parentId?: string | null } = {},
  ): Promise<KnowledgeTreeResponse> {
    const source = await this.source();
    const status = sourceStatus(source);
    if (status !== 'ready') {
      return {
        items: [],
        sourceStatus: status,
        lastCompleteScanAt: source.lastCompleteScan?.toISOString(),
      };
    }
    const limit = pageLimit(page);
    const rows = await this.models.KnowledgeNode.find({
      sourceId: this.sourceId,
      accessEpoch: source.accessEpoch,
      parentId: page.parentId || null,
      state: { $in: ['active', 'missing', 'out_of_scope'] },
      ...(page.cursor ? { id: { $gt: page.cursor } } : {}),
    })
      .sort({ id: 1 })
      .limit(limit + 1)
      .lean();
    const selected = rows.slice(0, limit);
    const documents = await this.models.KnowledgeDocument.find({
      sourceId: this.sourceId,
      id: { $in: selected.map((node) => node.documentId) },
    }).lean();
    const byId = new Map(documents.map((document) => [document.id, document]));
    const items: KnowledgeTreeResponse['items'] = [];
    for (const node of selected) {
      const document = byId.get(node.documentId);
      if (
        !document ||
        document.requiresRevalidation ||
        (document.activeRevisionId && document.accessEpoch !== source.accessEpoch) ||
        ['removed', 'inaccessible'].includes(document.status)
      ) {
        continue;
      }
      items.push({
        id: node.id,
        parentId: node.parentId || null,
        documentId: document.id,
        title: node.title,
        objectType: node.objType,
        status: document.status,
        readable:
          !!document.activeRevisionId &&
          document.accessEpoch === source.accessEpoch &&
          node.state !== 'out_of_scope',
        hasChildren: node.hasChildren,
        order: items.length,
      });
    }
    return {
      items,
      nextCursor: rows.length > limit ? selected[selected.length - 1]?.id : undefined,
      sourceStatus: status,
      lastCompleteScanAt: source.lastCompleteScan?.toISOString(),
    };
  }

  async activeDocument(id: string): Promise<KnowledgeDocumentRecord> {
    const source = await this.source(true);
    const document = await this.models.KnowledgeDocument.findOne({
      id,
      sourceId: this.sourceId,
      accessEpoch: source.accessEpoch,
      status: { $nin: ['removed', 'inaccessible'] },
      activeRevisionId: { $exists: true },
    }).lean();
    if (!document) {
      throw new KnowledgeError('KNOWLEDGE_DOCUMENT_UNAVAILABLE', 404);
    }
    return document;
  }

  async readDocument(
    id: string,
    input: { revisionId?: string } = {},
  ): Promise<KnowledgeDocumentResponse> {
    const document = await this.activeDocument(id);
    const revision = await this.models.KnowledgeRevision.findOne({
      id: input.revisionId ?? document.activeRevisionId,
      documentId: id,
      sourceId: this.sourceId,
      status: 'published',
    }).lean();
    if (!revision) {
      throw new KnowledgeError('KNOWLEDGE_REVISION_UNAVAILABLE', 404);
    }
    const snapshot = JSON.parse(
      (await this.blobs.read(revision.blobKey)).toString('utf8'),
    ) as KnowledgeSnapshot;
    const assets = await this.models.KnowledgeAsset.find({
      sourceId: this.sourceId,
      documentId: id,
      revisionId: revision.id,
      id: { $in: revision.assetIds },
    }).lean();
    await this.activeDocument(id);
    return {
      id,
      title: document.title,
      status: document.status,
      revision: {
        id: revision.id,
        sourceUpdatedAt: revision.sourceUpdatedAt?.toISOString(),
        extractedAt: revision.extractedAt.toISOString(),
        publishedAt: revision.publishedAt!.toISOString(),
      },
      blocks: snapshot.blocks,
      assets: assets.map((asset) => ({
        id: asset.id,
        mediaId: asset.id,
        name: asset.name,
        type: asset.contentType.startsWith('image/') ? 'image' : 'file',
        mimeType: asset.contentType,
        bytes: asset.size,
      })),
    };
  }

  async readAsset(id: string): Promise<KnowledgeAssetContent> {
    await this.source(true);
    const asset = await this.models.KnowledgeAsset.findOne({ id, sourceId: this.sourceId }).lean();
    if (!asset) {
      throw new KnowledgeError('KNOWLEDGE_ASSET_UNAVAILABLE', 404);
    }
    await this.activeDocument(asset.documentId);
    if (
      !(await this.models.KnowledgeRevision.exists({
        id: asset.revisionId,
        documentId: asset.documentId,
        sourceId: this.sourceId,
        status: 'published',
        assetIds: id,
      }))
    ) {
      throw new KnowledgeError('KNOWLEDGE_ASSET_UNAVAILABLE', 404);
    }
    const bytes = await this.blobs.read(asset.blobKey);
    await this.activeDocument(asset.documentId);
    return { bytes, contentType: asset.contentType, name: asset.name, size: bytes.length };
  }

  async activeFileIds(): Promise<string[]> {
    const source = await this.source(true);
    const documents = await this.models.KnowledgeDocument.find(
      {
        sourceId: this.sourceId,
        accessEpoch: source.accessEpoch,
        status: { $nin: ['removed', 'inaccessible'] },
        activeRevisionId: { $exists: true },
      },
      { activeRevisionId: 1 },
    ).lean();
    const revisions = await this.models.KnowledgeRevision.find(
      {
        sourceId: this.sourceId,
        status: 'published',
        id: { $in: documents.map((document) => document.activeRevisionId) },
      },
      { nativeFileIds: 1 },
    ).lean();
    return [...new Set(revisions.flatMap((revision) => revision.nativeFileIds))];
  }

  async listRuns(page: KnowledgePage = {}): Promise<KnowledgeSyncRunsResponse> {
    const source = await this.source();
    const limit = pageLimit(page);
    const runs = await this.models.KnowledgeRun.find({
      sourceId: this.sourceId,
      ...(page.cursor ? { id: { $lt: page.cursor } } : {}),
    })
      .sort({ id: -1 })
      .limit(limit + 1)
      .lean();
    return {
      items: runs.slice(0, limit).map(publicRun),
      nextCursor: runs.length > limit ? runs[limit - 1].id : undefined,
      sourceStatus: sourceStatus(source),
      lastCompleteScanAt: source.lastCompleteScan?.toISOString(),
    };
  }

  async getRun(id: string, page: KnowledgePage = {}): Promise<KnowledgeSyncRunResponse> {
    const run = await this.models.KnowledgeRun.findOne({ id, sourceId: this.sourceId }).lean();
    if (!run) {
      throw new KnowledgeError('KNOWLEDGE_RUN_UNAVAILABLE', 404);
    }
    const limit = pageLimit(page);
    const items = await this.models.KnowledgeItem.find({
      sourceId: this.sourceId,
      runId: id,
      $or: [{ kind: 'document' }, { kind: 'confirmation', status: 'failed' }],
      ...(page.cursor ? { id: { $gt: page.cursor } } : {}),
    })
      .sort({ id: 1 })
      .limit(limit + 1)
      .lean();
    const selected = items.slice(0, limit);
    const nodeIds = selected
      .filter((item) => item.kind === 'confirmation')
      .map((item) => item.nodeId);
    const nodes = nodeIds.length
      ? await this.models.KnowledgeNode.find({
          sourceId: this.sourceId,
          id: { $in: nodeIds },
        }).lean()
      : [];
    const nodeDocuments = new Map(nodes.map((node) => [node.id, node.documentId]));
    const documentIds = new Map(
      selected.map((item) => [item.id, item.documentId ?? nodeDocuments.get(item.nodeId ?? '')]),
    );
    const documents = await this.models.KnowledgeDocument.find({
      sourceId: this.sourceId,
      id: { $in: Array.from(documentIds.values()).filter(Boolean) },
    }).lean();
    const byId = new Map(documents.map((document) => [document.id, document]));
    return {
      ...publicRun(run),
      items: selected.flatMap((item) => {
        const documentId = documentIds.get(item.id);
        return documentId
          ? [
              {
                id: item.id,
                documentId,
                title: byId.get(documentId)?.title ?? '',
                status: itemDocumentStatus(item),
                errorCode: item.errorCode,
                missing: item.missing,
              },
            ]
          : [];
      }),
      nextCursor: items.length > limit ? selected[selected.length - 1]?.id : undefined,
    };
  }

  async refreshAgentFiles(session: ClientSession): Promise<void> {
    const source = await this.models.KnowledgeSource.findOne({ id: this.sourceId })
      .session(session)
      .lean();
    if (!source) {
      throw new KnowledgeError('KNOWLEDGE_SOURCE_UNCONFIGURED');
    }
    let fileIds: string[] = [];
    if (source.enabled && source.health === 'healthy') {
      const documents = await this.models.KnowledgeDocument.find(
        {
          sourceId: this.sourceId,
          accessEpoch: source.accessEpoch,
          status: { $nin: ['removed', 'inaccessible'] },
          activeRevisionId: { $exists: true },
        },
        { activeRevisionId: 1 },
      )
        .session(session)
        .lean();
      const revisions = await this.models.KnowledgeRevision.find(
        {
          sourceId: this.sourceId,
          status: 'published',
          id: { $in: documents.map((document) => document.activeRevisionId) },
        },
        { nativeFileIds: 1 },
      )
        .session(session)
        .lean();
      fileIds = [...new Set(revisions.flatMap((revision) => revision.nativeFileIds))];
    }
    const result = await this.models.Agent.updateOne(
      { id: source.agentId },
      { $set: { 'tool_resources.file_search.file_ids': fileIds } },
      { session },
    );
    if (result.matchedCount !== 1) {
      throw new KnowledgeError('KNOWLEDGE_AGENT_UNAVAILABLE');
    }
  }

  async getSourceHealth(): Promise<{
    sourceStatus: KnowledgeSourceStatus;
    lastCompleteScanAt?: string;
    errorCode?: string;
  }> {
    const source = await this.source();
    return {
      sourceStatus: sourceStatus(source),
      lastCompleteScanAt: source.lastCompleteScan?.toISOString(),
      errorCode: source.errorCode,
    };
  }
}

export type KnowledgeStoredRevision = KnowledgeRevisionRecord;
export type KnowledgeStoredItem = KnowledgeItemRecord;
