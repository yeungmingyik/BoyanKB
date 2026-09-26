import { createHash, randomUUID } from 'node:crypto';
import type {
  KnowledgeRunRecord,
  KnowledgeDocumentRecord,
  KnowledgeItemRecord,
  KnowledgeAssetRecord,
} from '@librechat/data-schemas';
import type {
  TKnowledgeSyncConfig,
  KnowledgeReadingBlock,
  KnowledgeSyncRun,
} from 'librechat-data-provider';
import type { ClientSession } from 'mongoose';
import type { KnowledgeLease, KnowledgeServiceModels } from './store';
import type { FeishuReader, FeishuNode } from './feishu';
import type { KnowledgeBlobStore } from './blob';
import {
  KnowledgeStore,
  KnowledgeError,
  knowledgeId,
  knowledgeRunId,
  emptyKnowledgeCounts,
} from './store';
import { extractDocx } from './extract';
import { FeishuError } from './feishu';

export interface KnowledgeIndexer {
  version: string;
  index(input: {
    documentId: string;
    revisionId: string;
    title: string;
    text: string;
    idempotencyKey: string;
  }): Promise<{ fileIds: string[]; indexedAt?: Date }>;
  verify(fileIds: string[]): Promise<boolean>;
  remove?(fileIds: string[]): Promise<void>;
}

export interface KnowledgeServiceConfig {
  agentId: string;
  sourceId?: string;
  sync: TKnowledgeSyncConfig;
}

export interface KnowledgeServiceOptions {
  models: KnowledgeServiceModels;
  blobs: KnowledgeBlobStore;
  client: FeishuReader;
  indexer: KnowledgeIndexer;
  config: KnowledgeServiceConfig;
  now?: () => Date;
  workerId?: string;
}

function errorCode(error: unknown): string {
  if (error instanceof KnowledgeError || error instanceof FeishuError) {
    return error.code;
  }
  const code = (error as { code?: unknown })?.code;
  return typeof code === 'string' && /^KNOWLEDGE_INDEX_[A-Z_]{1,64}$/.test(code)
    ? code
    : 'KNOWLEDGE_SYNC_FAILED';
}

function sourceFailure(error: unknown): boolean {
  return (
    error instanceof FeishuError &&
    (['auth', 'source_denied'].includes(error.kind) ||
      (error.scope === 'source' && error.kind === 'not_found'))
  );
}

function replaceMedia(
  blocks: KnowledgeReadingBlock[],
  ids: Map<string, string>,
): KnowledgeReadingBlock[] {
  return blocks.map((block) => ({
    ...block,
    ...(block.mediaId ? { mediaId: ids.get(block.mediaId) } : {}),
    ...(block.children ? { children: replaceMedia(block.children, ids) } : {}),
  }));
}

export class KnowledgeService extends KnowledgeStore {
  private readonly client: FeishuReader;
  private readonly indexer: KnowledgeIndexer;
  private readonly config: KnowledgeServiceConfig;
  private readonly now: () => Date;
  private readonly workerId: string;

  constructor(options: KnowledgeServiceOptions) {
    super(
      options.models,
      options.blobs,
      options.config.sourceId ??
        knowledgeId('source', options.config.sync.spaceId || `primary:${options.config.agentId}`),
    );
    this.client = options.client;
    this.indexer = options.indexer;
    this.config = options.config;
    this.now = options.now ?? (() => new Date());
    this.workerId = options.workerId ?? randomUUID();
  }

  async ensureSource(): Promise<Awaited<ReturnType<KnowledgeStore['ensureSource']>>> {
    await this.initialize();
    const existing = await this.models.KnowledgeSource.findOne({
      agentId: this.config.agentId,
    }).lean();
    if (existing && !this.config.sourceId) {
      this.sourceId = existing.id;
    }
    return super.ensureSource({
      agentId: this.config.agentId,
      spaceId: this.config.sync.spaceId,
      enabled: this.config.sync.enabled,
    });
  }

  async tick(): Promise<KnowledgeSyncRun | null> {
    const source = await this.source();
    if (!source.enabled) {
      return null;
    }
    const now = this.now().getTime();
    if (
      !(await this.models.KnowledgeRun.exists({
        sourceId: this.sourceId,
        status: { $in: ['queued', 'running'] },
      }))
    ) {
      if (
        !source.lastReconcileAt ||
        now - source.lastReconcileAt.getTime() >= this.config.sync.reconcileIntervalMs
      ) {
        await this.enqueue({
          mode: 'full',
          idempotencyKey: `scheduled-full:${Math.floor(now / this.config.sync.pollIntervalMs)}`,
        });
      } else if (
        !source.lastPollAt ||
        now - source.lastPollAt.getTime() >= this.config.sync.pollIntervalMs
      ) {
        await this.enqueue({
          mode: 'incremental',
          idempotencyKey: `scheduled-poll:${Math.floor(now / this.config.sync.pollIntervalMs)}`,
        });
      }
    }
    return this.processNext();
  }

  async processNext(input: { maxSteps?: number } = {}): Promise<KnowledgeSyncRun | null> {
    const lease = await this.acquire(this.workerId, this.config.sync.leaseMs);
    if (!lease) {
      return null;
    }
    let leaseLost = false;
    let heartbeatBusy = false;
    const heartbeat = setInterval(
      () => {
        if (heartbeatBusy) {
          return;
        }
        heartbeatBusy = true;
        void this.renew(lease, this.config.sync.leaseMs)
          .catch(() => {
            leaseLost = true;
          })
          .finally(() => {
            heartbeatBusy = false;
          });
      },
      Math.max(1000, Math.floor(this.config.sync.leaseMs / 3)),
    );
    heartbeat.unref();
    let run: KnowledgeRunRecord | null = null;
    try {
      run = await this.mutate(lease, async (session) => {
        const source = await this.models.KnowledgeSource.findOne({ id: this.sourceId })
          .session(session)
          .lean();
        let current = source?.activeRunId
          ? await this.models.KnowledgeRun.findOne({
              id: source.activeRunId,
              sourceId: this.sourceId,
              status: { $in: ['queued', 'running'] },
            })
              .session(session)
              .lean()
          : null;
        current ??= await this.models.KnowledgeRun.findOne({
          sourceId: this.sourceId,
          status: 'queued',
        })
          .sort({ createdAt: 1, id: 1 })
          .session(session)
          .lean();
        if (!current) {
          return null;
        }
        await this.models.KnowledgeSource.updateOne(
          { id: this.sourceId },
          { $set: { activeRunId: current.id } },
          { session },
        );
        await this.models.KnowledgeRun.updateOne(
          { id: current.id },
          { $set: { status: 'running', startedAt: current.startedAt ?? this.now() } },
          { session },
        );
        return current;
      });
      if (!run) {
        return null;
      }
      for (let step = 0; step < (input.maxSteps ?? 1000); step++) {
        if (leaseLost) {
          throw new KnowledgeError('KNOWLEDGE_LEASE_LOST', 409);
        }
        run = (await this.models.KnowledgeRun.findOne({
          id: run.id,
          sourceId: this.sourceId,
        }).lean())!;
        if (!['queued', 'running'].includes(run.status)) {
          break;
        }
        if (!(await this.advance(lease, run))) {
          break;
        }
      }
      return this.getRun(run.id);
    } catch (error) {
      if (error instanceof KnowledgeError && error.code === 'KNOWLEDGE_LEASE_LOST') {
        throw error;
      }
      if (run) {
        await this.mutate(lease, async (session) => {
          if (sourceFailure(error)) {
            const source = await this.models.KnowledgeSource.findOne({ id: this.sourceId })
              .session(session)
              .lean();
            await this.models.KnowledgeSource.updateOne(
              { id: this.sourceId },
              {
                $set: { health: 'paused', errorCode: errorCode(error) },
                ...(source?.health !== 'paused' ? { $inc: { accessEpoch: 1 } } : {}),
              },
              { session },
            );
            await this.refreshAgentFiles(session);
          }
          await this.models.KnowledgeRun.updateOne(
            { id: run!.id },
            { $set: { status: 'failed', errorCode: errorCode(error), finishedAt: this.now() } },
            { session },
          );
          await this.models.KnowledgeSource.updateOne(
            { id: this.sourceId },
            { $unset: { activeRunId: 1 } },
            { session },
          );
        });
        return this.getRun(run.id);
      }
      throw error;
    } finally {
      clearInterval(heartbeat);
      await this.release(lease);
    }
  }

  private async advance(lease: KnowledgeLease, run: KnowledgeRunRecord): Promise<boolean> {
    if (run.phase === 'validate') {
      const resolved = await this.client.resolveSpace(
        this.config.sync.wikiUrl,
        this.config.sync.spaceId || undefined,
      );
      const source = await this.source();
      if (source.spaceId && source.spaceId !== resolved.spaceId) {
        throw new KnowledgeError('KNOWLEDGE_SOURCE_MISMATCH', 409);
      }
      await this.mutate(lease, async (session) => {
        await this.models.KnowledgeSource.updateOne(
          { id: this.sourceId },
          { $set: { spaceId: resolved.spaceId, health: 'healthy' }, $unset: { errorCode: 1 } },
          { session },
        );
        await this.addItem(session, run.id, 'folder', 'root');
        await this.models.KnowledgeRun.updateOne(
          { id: run.id },
          { $set: { phase: 'enumerate' } },
          { session },
        );
        await this.refreshAgentFiles(session);
      });
      return true;
    }
    if (run.phase === 'enumerate') {
      const item = await this.nextItem(run.id, 'folder');
      if (item) {
        return this.performItem(lease, run, item, () => this.enumeratePage(lease, run, item));
      }
      await this.mutate(lease, async (session) => {
        await this.models.KnowledgeRun.updateOne(
          { id: run.id },
          { $set: { enumerationComplete: true, phase: 'extract' } },
          { session },
        );
      });
      return true;
    }
    if (run.phase === 'extract') {
      const item = await this.nextItem(run.id, 'document');
      if (item) {
        return this.performItem(lease, run, item, () => this.extractDocument(lease, run, item));
      }
      await this.mutate(lease, async (session) => {
        await this.models.KnowledgeRun.updateOne(
          { id: run.id },
          { $set: { phase: 'reconcile' } },
          { session },
        );
      });
      return true;
    }
    if (run.phase === 'reconcile') {
      const item = await this.nextItem(run.id, 'confirmation');
      if (item) {
        return this.performItem(lease, run, item, () => this.confirmMissing(lease, run, item));
      }
      if (run.mode === 'full') {
        const queued = await this.mutate(lease, async (session) => {
          const missing = await this.models.KnowledgeNode.find({
            sourceId: this.sourceId,
            lastSeenRunId: { $ne: run.id },
            state: { $in: ['active', 'missing'] },
            ...(run.reconcileCursor ? { id: { $gt: run.reconcileCursor } } : {}),
          })
            .sort({ id: 1 })
            .limit(100)
            .session(session)
            .lean();
          for (const node of missing) {
            await this.addItem(session, run.id, 'confirmation', node.id, { nodeId: node.id });
          }
          if (missing.length) {
            await this.models.KnowledgeRun.updateOne(
              { id: run.id },
              { $set: { reconcileCursor: missing[missing.length - 1].id } },
              { session },
            );
          }
          return missing.length;
        });
        if (queued) {
          return true;
        }
      }
      await this.finish(lease, run);
      return false;
    }
    return false;
  }

  private async addItem(
    session: ClientSession,
    runId: string,
    kind: KnowledgeItemRecord['kind'],
    key: string,
    fields: Partial<KnowledgeItemRecord> = {},
  ): Promise<void> {
    await this.models.KnowledgeItem.updateOne(
      { runId, kind, key },
      {
        $setOnInsert: {
          id: knowledgeId('item', runId, kind, key),
          sourceId: this.sourceId,
          runId,
          kind,
          key,
          ...fields,
        },
      },
      { upsert: true, session },
    );
  }

  private async nextItem(
    runId: string,
    kind: KnowledgeItemRecord['kind'],
  ): Promise<KnowledgeItemRecord | null> {
    return this.models.KnowledgeItem.findOne({
      sourceId: this.sourceId,
      runId,
      kind,
      status: { $in: ['pending', 'running'] },
    })
      .sort({ id: 1 })
      .lean();
  }

  private async performItem(
    lease: KnowledgeLease,
    run: KnowledgeRunRecord,
    item: KnowledgeItemRecord,
    work: () => Promise<void>,
  ): Promise<boolean> {
    if (item.retryAt && item.retryAt > this.now()) {
      return false;
    }
    try {
      await work();
      return true;
    } catch (error) {
      if (
        sourceFailure(error) ||
        (error instanceof KnowledgeError && error.code === 'KNOWLEDGE_LEASE_LOST')
      ) {
        throw error;
      }
      if (
        item.kind === 'document' &&
        error instanceof FeishuError &&
        ['document_denied', 'not_found'].includes(error.kind)
      ) {
        await this.mutate(lease, async (session) => {
          await this.models.KnowledgeDocument.updateOne(
            { id: item.documentId, sourceId: this.sourceId },
            {
              $set: {
                status: error.kind === 'document_denied' ? 'inaccessible' : 'removed',
                accessEpoch: -1,
                requiresRevalidation: true,
                errorCode: error.code,
              },
            },
            { session },
          );
          await this.models.KnowledgeItem.updateOne(
            { id: item.id },
            { $set: { status: 'failed', errorCode: error.code } },
            { session },
          );
          await this.refreshAgentFiles(session);
        });
        return true;
      }
      const retryable = error instanceof FeishuError && error.kind === 'transient';
      if (retryable && item.attempts < this.config.sync.maxRetries) {
        await this.mutate(lease, async (session) => {
          await this.models.KnowledgeItem.updateOne(
            { id: item.id },
            {
              $inc: { attempts: 1 },
              $set: {
                errorCode: error.code,
                retryAt: new Date(
                  this.now().getTime() +
                    Math.max(error.retryAfterMs ?? 0, 1000 * 2 ** item.attempts),
                ),
              },
            },
            { session },
          );
        });
        return false;
      }
      if (item.kind === 'folder') {
        throw error;
      }
      await this.mutate(lease, async (session) => {
        await this.models.KnowledgeItem.updateOne(
          { id: item.id },
          { $set: { status: 'failed', errorCode: errorCode(error) } },
          { session },
        );
        if (item.documentId) {
          await this.models.KnowledgeDocument.updateOne(
            { id: item.documentId, sourceId: this.sourceId },
            { $set: { status: 'failed', errorCode: errorCode(error) } },
            { session },
          );
        }
      });
      return true;
    }
  }

  private async enumeratePage(
    lease: KnowledgeLease,
    run: KnowledgeRunRecord,
    item: KnowledgeItemRecord,
  ): Promise<void> {
    const source = await this.source();
    const page = await this.client.listNodes(source.spaceId, item.parentToken, item.cursor);
    if (
      page.hasMore &&
      (!page.pageToken ||
        item.cursorHistory.includes(knowledgeId('cursor', page.pageToken)) ||
        page.pageToken === item.cursor)
    ) {
      throw new KnowledgeError('KNOWLEDGE_PAGE_CYCLE');
    }
    if (
      page.items.some(
        (node) => node.space_id !== source.spaceId || node.node_token === item.parentToken,
      )
    ) {
      throw new KnowledgeError('KNOWLEDGE_NODE_SCOPE_INVALID');
    }
    await this.mutate(lease, async (session) => {
      for (const node of page.items) {
        await this.discover(session, run, item, node, source.accessEpoch);
      }
      const count = await this.models.KnowledgeNode.countDocuments({
        sourceId: this.sourceId,
        lastSeenRunId: run.id,
      }).session(session);
      if (count > this.config.sync.maxNodes) {
        throw new KnowledgeError('KNOWLEDGE_NODE_LIMIT');
      }
      await this.models.KnowledgeItem.updateOne(
        { id: item.id },
        {
          $set: {
            cursor: page.hasMore ? page.pageToken : undefined,
            cursorHistory: [
              ...item.cursorHistory,
              ...(item.cursor ? [knowledgeId('cursor', item.cursor)] : []),
            ],
            status: page.hasMore ? 'pending' : 'succeeded',
          },
          $unset: { retryAt: 1, errorCode: 1 },
        },
        { session },
      );
    });
  }

  private async discover(
    session: ClientSession,
    run: KnowledgeRunRecord,
    parent: KnowledgeItemRecord,
    node: FeishuNode,
    accessEpoch: number,
  ): Promise<void> {
    const id = knowledgeId('node', this.sourceId, node.node_token);
    const parentId = parent.parentToken
      ? knowledgeId('node', this.sourceId, parent.parentToken)
      : undefined;
    const existing = await this.models.KnowledgeNode.findOne({ id }).session(session).lean();
    if (existing?.lastSeenRunId === run.id && existing.parentId !== parentId) {
      throw new KnowledgeError('KNOWLEDGE_NODE_CYCLE');
    }
    const documentId = knowledgeId('doc', this.sourceId, node.obj_type, node.obj_token);
    const inScope = !node.origin_space_id || node.origin_space_id === node.space_id;
    await this.models.KnowledgeNode.updateOne(
      { id },
      {
        $set: {
          sourceId: this.sourceId,
          nodeToken: node.node_token,
          ...(parentId ? { parentId } : {}),
          documentId,
          title: node.title,
          objType: node.obj_type,
          hasChildren: node.has_child && inScope,
          lastSeenRunId: run.id,
          accessEpoch,
          state: inScope ? 'active' : 'out_of_scope',
        },
        $unset: { missingRunId: 1, ...(!parentId ? { parentId: 1 } : {}) },
      },
      { upsert: true, session },
    );
    if (
      existing &&
      existing.documentId !== documentId &&
      !(await this.models.KnowledgeNode.exists({
        sourceId: this.sourceId,
        documentId: existing.documentId,
        state: { $in: ['active', 'missing'] },
      }).session(session))
    ) {
      await this.models.KnowledgeDocument.updateOne(
        { id: existing.documentId },
        {
          $set: {
            status: 'removed',
            accessEpoch: -1,
            requiresRevalidation: true,
            errorCode: 'KNOWLEDGE_DOCUMENT_REMOVED',
          },
        },
        { session },
      );
      await this.refreshAgentFiles(session);
    }
    const sourceUpdatedAt =
      node.obj_edit_time && /^\d+$/.test(node.obj_edit_time)
        ? new Date(Number(node.obj_edit_time) * 1000)
        : undefined;
    await this.models.KnowledgeDocument.updateOne(
      { id: documentId },
      {
        $set: { ...(sourceUpdatedAt ? { sourceUpdatedAt } : {}) },
        $setOnInsert: {
          id: documentId,
          sourceId: this.sourceId,
          title: node.title,
          objType: node.obj_type,
          objToken: node.obj_token,
          status: inScope && node.obj_type === 'docx' ? 'pending' : 'unsupported',
          ...(!inScope ? { errorCode: 'KNOWLEDGE_OUT_OF_SCOPE' } : {}),
        },
      },
      { upsert: true, session },
    );
    await this.addItem(session, run.id, 'document', documentId, { documentId });
    if (inScope) {
      if (node.has_child) {
        await this.addItem(session, run.id, 'folder', node.node_token, {
          parentToken: node.node_token,
        });
      }
    } else if (
      !(await this.models.KnowledgeNode.exists({
        sourceId: this.sourceId,
        documentId,
        state: { $in: ['active', 'missing'] },
      }).session(session))
    ) {
      await this.models.KnowledgeDocument.updateOne(
        { id: documentId },
        { $set: { status: 'unsupported', accessEpoch: -1, errorCode: 'KNOWLEDGE_OUT_OF_SCOPE' } },
        { session },
      );
      await this.refreshAgentFiles(session);
    }
  }

  private async extractDocument(
    lease: KnowledgeLease,
    run: KnowledgeRunRecord,
    item: KnowledgeItemRecord,
  ): Promise<void> {
    const document = (await this.models.KnowledgeDocument.findOne({
      id: item.documentId,
      sourceId: this.sourceId,
    }).lean())!;
    const inScope = await this.models.KnowledgeNode.exists({
      sourceId: this.sourceId,
      documentId: document.id,
      state: { $in: ['active', 'missing'] },
    });
    if (document.objType !== 'docx' || !inScope) {
      const code = inScope ? 'KNOWLEDGE_FORMAT_UNSUPPORTED' : 'KNOWLEDGE_OUT_OF_SCOPE';
      await this.mutate(lease, async (session) => {
        await this.models.KnowledgeDocument.updateOne(
          { id: document.id },
          { $set: { status: 'unsupported', errorCode: code, lastAttemptRunId: run.id } },
          { session },
        );
        await this.models.KnowledgeItem.updateOne(
          { id: item.id },
          { $set: { status: 'partial', errorCode: code } },
          { session },
        );
      });
      return;
    }
    await this.mutate(lease, async (session) => {
      await this.models.KnowledgeDocument.updateOne(
        { id: document.id },
        { $set: { status: 'extracting', lastAttemptRunId: run.id } },
        { session },
      );
    });
    for (let attempt = 0; attempt <= this.config.sync.maxRetries; attempt++) {
      const before = await this.client.getDocument(document.objToken);
      const extracted = extractDocx(await this.client.getBlocks(document.objToken));
      const downloaded: {
        mediaId: string;
        blobKey: string;
        hash: string;
        contentType: string;
        name: string;
        size: number;
      }[] = [];
      for (const media of extracted.media) {
        try {
          const content = await this.client.downloadMedia(media.token);
          if (content.buffer.length > this.config.sync.maxAssetBytes) {
            throw new FeishuError('limit_exceeded');
          }
          const blob = await this.blobs.put(content.buffer);
          downloaded.push({
            mediaId: media.id,
            blobKey: blob.key,
            hash: blob.hash,
            contentType: content.contentType,
            name: media.name || `${media.type}-${media.id}`,
            size: blob.size,
          });
        } catch (error) {
          if (sourceFailure(error)) {
            throw error;
          }
          if (error instanceof FeishuError && error.kind === 'document_denied') {
            throw error;
          }
          extracted.complete = false;
          extracted.missing.push({
            blockId: media.blockId,
            blockType: 0,
            reason: errorCode(error),
          });
        }
      }
      const after = await this.client.getDocument(document.objToken);
      if (before.revision_id !== after.revision_id) {
        continue;
      }
      const hash = createHash('sha256')
        .update(
          JSON.stringify({
            blocks: extracted.blocks,
            text: extracted.text,
            media: downloaded.map(({ mediaId, hash: mediaHash, contentType, name }) => ({
              mediaId,
              hash: mediaHash,
              contentType,
              name,
            })),
            missing: extracted.missing,
          }),
        )
        .digest('hex');
      const idempotencyKey = knowledgeId(
        'snapshot',
        this.sourceId,
        document.objType,
        document.objToken,
        hash,
        extracted.parserVersion,
        this.indexer.version,
      );
      const revisionId = knowledgeId('rev', document.id, idempotencyKey);
      const ids = new Map(
        downloaded.map((media) => [media.mediaId, knowledgeId('asset', revisionId, media.mediaId)]),
      );
      const assets: KnowledgeAssetRecord[] = downloaded.map((media) => ({
        id: ids.get(media.mediaId)!,
        sourceId: this.sourceId,
        documentId: document.id,
        revisionId,
        mediaId: media.mediaId,
        blobKey: media.blobKey,
        contentType: media.contentType,
        name: media.name,
        size: media.size,
      }));
      const snapshot = await this.blobs.put(
        Buffer.from(JSON.stringify({ blocks: replaceMedia(extracted.blocks, ids) })),
      );
      await this.mutate(lease, async (session) => {
        await this.models.KnowledgeRevision.updateOne(
          { id: revisionId },
          {
            $setOnInsert: {
              id: revisionId,
              sourceId: this.sourceId,
              documentId: document.id,
              idempotencyKey,
              sourceRevision: String(after.revision_id),
              sourceUpdatedAt: document.sourceUpdatedAt,
              contentHash: hash,
              blobKey: snapshot.key,
              text: extracted.text,
              parserVersion: extracted.parserVersion,
              indexVersion: this.indexer.version,
              assetIds: assets.map((asset) => asset.id),
              complete: extracted.complete,
              missing: extracted.missing,
              status: extracted.complete ? 'staged' : 'partial',
              extractedAt: this.now(),
            },
          },
          { upsert: true, session },
        );
        await this.models.KnowledgeItem.updateOne(
          { id: item.id },
          { $set: { revisionId, missing: extracted.missing } },
          { session },
        );
        for (const asset of assets) {
          await this.models.KnowledgeAsset.updateOne(
            { id: asset.id },
            { $setOnInsert: asset },
            { upsert: true, session },
          );
        }
        if (!extracted.complete) {
          await this.models.KnowledgeDocument.updateOne(
            { id: document.id },
            { $set: { status: 'partial', errorCode: 'KNOWLEDGE_EXTRACTION_PARTIAL' } },
            { session },
          );
          await this.models.KnowledgeItem.updateOne(
            { id: item.id },
            { $set: { status: 'partial', errorCode: 'KNOWLEDGE_EXTRACTION_PARTIAL' } },
            { session },
          );
        }
      });
      if (!extracted.complete) {
        return;
      }
      const title =
        typeof after.title === 'string' && after.title.trim() ? after.title : document.title;
      await this.publish(lease, item, document, revisionId, title);
      return;
    }
    throw new KnowledgeError('KNOWLEDGE_SOURCE_VERSION_CHANGED');
  }

  private async publish(
    lease: KnowledgeLease,
    item: KnowledgeItemRecord,
    document: KnowledgeDocumentRecord,
    revisionId: string,
    title: string,
  ): Promise<void> {
    const revision = (await this.models.KnowledgeRevision.findOne({
      id: revisionId,
      sourceId: this.sourceId,
    }).lean())!;
    await this.blobs.read(revision.blobKey);
    for (const asset of await this.models.KnowledgeAsset.find({
      sourceId: this.sourceId,
      revisionId,
    }).lean()) {
      await this.blobs.read(asset.blobKey);
    }
    let fileIds = revision.nativeFileIds;
    let indexedAt = revision.indexedAt;
    if (!fileIds.length || !(await this.indexer.verify(fileIds))) {
      await this.mutate(lease, async (session) => {
        await this.models.KnowledgeDocument.updateOne(
          { id: document.id },
          { $set: { status: 'indexing' } },
          { session },
        );
      });
      const indexed = await this.indexer.index({
        documentId: document.id,
        revisionId,
        title,
        text: revision.text,
        idempotencyKey: revision.idempotencyKey,
      });
      fileIds = indexed.fileIds;
      indexedAt = indexed.indexedAt ?? this.now();
    }
    if (!fileIds.length || !(await this.indexer.verify(fileIds))) {
      throw new KnowledgeError('KNOWLEDGE_INDEX_UNAVAILABLE');
    }
    await this.mutate(lease, async (session) => {
      const source = await this.models.KnowledgeSource.findOne({ id: this.sourceId })
        .session(session)
        .lean();
      if (source?.health !== 'healthy') {
        throw new KnowledgeError('KNOWLEDGE_SOURCE_UNAVAILABLE');
      }
      await this.models.KnowledgeRevision.updateOne(
        { id: revisionId, complete: true },
        {
          $set: {
            nativeFileIds: fileIds,
            indexedAt,
            status: 'published',
            publishedAt: revision.publishedAt ?? this.now(),
          },
        },
        { session },
      );
      await this.models.KnowledgeDocument.updateOne(
        { id: document.id, sourceId: this.sourceId },
        {
          $set: {
            activeRevisionId: revisionId,
            title,
            accessEpoch: source.accessEpoch,
            requiresRevalidation: false,
            status: 'published',
            publishedAt: this.now(),
          },
          $unset: { errorCode: 1 },
        },
        { session },
      );
      await this.models.KnowledgeItem.updateOne(
        { id: item.id },
        {
          $set: {
            status: 'succeeded',
            outcome: document.activeRevisionId === revisionId ? 'unchanged' : 'published',
          },
          $unset: { errorCode: 1, retryAt: 1 },
        },
        { session },
      );
      await this.refreshAgentFiles(session);
    });
  }

  private async confirmMissing(
    lease: KnowledgeLease,
    run: KnowledgeRunRecord,
    item: KnowledgeItemRecord,
  ): Promise<void> {
    if (!run.enumerationComplete) {
      throw new KnowledgeError('KNOWLEDGE_SCAN_INCOMPLETE');
    }
    const node = await this.models.KnowledgeNode.findOne({
      id: item.nodeId,
      sourceId: this.sourceId,
    }).lean();
    if (!node || node.lastSeenRunId === run.id) {
      await this.mutate(lease, async (session) => {
        await this.models.KnowledgeItem.updateOne(
          { id: item.id },
          { $set: { status: 'succeeded' } },
          { session },
        );
      });
      return;
    }
    const source = await this.source();
    let state: 'missing' | 'removed' | 'inaccessible' = 'missing';
    try {
      const current = await this.client.getNode(node.nodeToken);
      if (
        current.space_id !== source.spaceId ||
        (current.origin_space_id && current.origin_space_id !== source.spaceId)
      ) {
        state = 'removed';
      } else if (node.missingRunId && node.missingRunId !== run.id) {
        const prior = await this.models.KnowledgeRun.findOne({
          id: node.missingRunId,
          sourceId: this.sourceId,
          enumerationComplete: true,
          status: { $in: ['succeeded', 'partial'] },
        }).lean();
        if (prior) {
          state = 'removed';
        }
      }
    } catch (error) {
      if (sourceFailure(error)) {
        throw error;
      }
      if (error instanceof FeishuError && ['not_found', 'document_denied'].includes(error.kind)) {
        state = error.kind === 'document_denied' ? 'inaccessible' : 'removed';
      } else {
        throw error;
      }
    }
    await this.mutate(lease, async (session) => {
      await this.models.KnowledgeNode.updateOne(
        { id: node.id },
        { $set: { state, missingRunId: run.id } },
        { session },
      );
      if (
        state !== 'missing' &&
        !(await this.models.KnowledgeNode.exists({
          sourceId: this.sourceId,
          documentId: node.documentId,
          state: { $in: ['active', 'missing'] },
        }).session(session))
      ) {
        await this.models.KnowledgeDocument.updateOne(
          { id: node.documentId, sourceId: this.sourceId },
          {
            $set: {
              status: state,
              accessEpoch: -1,
              requiresRevalidation: true,
              errorCode:
                state === 'removed' ? 'KNOWLEDGE_DOCUMENT_REMOVED' : 'FEISHU_DOCUMENT_DENIED',
            },
          },
          { session },
        );
        await this.refreshAgentFiles(session);
      }
      await this.models.KnowledgeItem.updateOne(
        { id: item.id },
        { $set: { status: 'succeeded', outcome: state } },
        { session },
      );
    });
  }

  private async finish(lease: KnowledgeLease, run: KnowledgeRunRecord): Promise<void> {
    await this.mutate(lease, async (session) => {
      const items = await this.models.KnowledgeItem.find({ sourceId: this.sourceId, runId: run.id })
        .session(session)
        .lean();
      if (
        !run.enumerationComplete ||
        items.some((item) => ['pending', 'running'].includes(item.status))
      ) {
        throw new KnowledgeError('KNOWLEDGE_SCAN_INCOMPLETE');
      }
      const documents = await this.models.KnowledgeDocument.find({
        sourceId: this.sourceId,
        id: {
          $in: items.filter((item) => item.kind === 'document').map((item) => item.documentId),
        },
      })
        .session(session)
        .lean();
      const counts = emptyKnowledgeCounts();
      counts.nodes = await this.models.KnowledgeNode.countDocuments({
        sourceId: this.sourceId,
        lastSeenRunId: run.id,
      }).session(session);
      counts.documents = documents.length;
      counts.published = items.filter((item) => item.outcome === 'published').length;
      counts.unchanged = items.filter((item) => item.outcome === 'unchanged').length;
      counts.failed = items.filter((item) => item.status === 'failed').length;
      counts.unsupported = documents.filter((document) => document.status === 'unsupported').length;
      counts.inaccessible = documents.filter(
        (document) => document.status === 'inaccessible',
      ).length;
      counts.removed = items.filter((item) => item.outcome === 'removed').length;
      counts.media = await this.models.KnowledgeAsset.countDocuments({
        sourceId: this.sourceId,
        revisionId: { $in: documents.map((document) => document.activeRevisionId).filter(Boolean) },
      }).session(session);
      const partial = items.some((item) => ['failed', 'partial'].includes(item.status));
      await this.models.KnowledgeRun.updateOne(
        { id: run.id },
        {
          $set: {
            status: partial ? 'partial' : 'succeeded',
            phase: 'finish',
            counts,
            finishedAt: this.now(),
          },
        },
        { session },
      );
      await this.models.KnowledgeSource.updateOne(
        { id: this.sourceId },
        {
          $set: {
            lastPollAt: this.now(),
            ...(run.mode === 'full'
              ? { lastCompleteScan: this.now(), lastReconcileAt: this.now() }
              : {}),
          },
          $unset: { activeRunId: 1 },
        },
        { session },
      );
      await this.models.KnowledgeAudit.create(
        [
          {
            id: randomUUID(),
            sourceId: this.sourceId,
            actorId: run.actorId,
            action: 'sync',
            resourceId: run.id,
            result: partial ? 'partial' : 'succeeded',
            at: this.now(),
          },
        ],
        { session },
      );
      if (items.some((item) => item.outcome === 'missing')) {
        await this.models.KnowledgeRun.updateOne(
          { sourceId: this.sourceId, idempotencyKey: `confirm:${run.id}` },
          {
            $setOnInsert: {
              id: knowledgeRunId(),
              sourceId: this.sourceId,
              idempotencyKey: `confirm:${run.id}`,
              mode: 'full',
              counts: emptyKnowledgeCounts(),
            },
          },
          { upsert: true, session },
        );
      }
    });
  }
}

export function createKnowledgeService(options: KnowledgeServiceOptions): KnowledgeService {
  return new KnowledgeService(options);
}
