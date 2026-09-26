import { createHash } from 'node:crypto';
import { knowledgeSearchConfigSchema } from 'librechat-data-provider';
import type {
  KnowledgeReadingBlock,
  KnowledgeSearchHit,
  KnowledgeSearchRequest,
  KnowledgeSearchResponse,
  TKnowledgeSearchConfig,
} from 'librechat-data-provider';
import type {
  KnowledgeDocumentRecord,
  KnowledgeNodeRecord,
  KnowledgeRevisionRecord,
} from '@librechat/data-schemas';
import { KnowledgeError, KnowledgeStore } from './store';

export interface KnowledgeSemanticSearch {
  query(input: {
    query: string;
    fileIds: string[];
    limit: number;
    signal?: AbortSignal;
  }): Promise<{ fileId: string; text: string; score: number }[]>;
}

export type KnowledgeSearchReference = Pick<
  KnowledgeSearchHit,
  'documentId' | 'revisionId' | 'blockId'
>;

type SearchDocument = {
  document: KnowledgeDocumentRecord;
  revision: KnowledgeRevisionRecord;
};

type Manifest = {
  snapshotId: string;
  documents: SearchDocument[];
  nodes: KnowledgeNodeRecord[];
};

type TextBlock = { id: string; text: string; start: number; end: number };

type WordSegmenter = {
  segment(value: string): Iterable<{ segment: string; isWordLike?: boolean }>;
};

const { Segmenter } = Intl as typeof Intl & {
  Segmenter: new (locale: string, options: { granularity: 'word' }) => WordSegmenter;
};

const nodeIdPattern = /^node_[a-f0-9]{64}$/;
const ignoredTerms = new Set([
  '怎么',
  '如何',
  '哪些',
  '什么',
  '多少',
  '是否',
  '可以',
  '请问',
  '介绍',
  '一下',
  '我们',
  '你们',
  '他们',
  '这个',
  '那个',
  '以及',
  '有关',
  '关于',
  '进行',
  'the',
  'and',
  'for',
  'what',
  'how',
  'are',
  'with',
  'this',
  'that',
  'does',
]);

function digest(value: unknown): string {
  return createHash('sha256').update(JSON.stringify(value)).digest('hex');
}

function terms(query: string): string[] {
  const segmenter = new Segmenter('zh', { granularity: 'word' });
  return [
    ...new Set([
      query.toLocaleLowerCase(),
      ...Array.from(segmenter.segment(query), ({ segment, isWordLike }) =>
        isWordLike ? segment.toLocaleLowerCase() : '',
      ).filter((word) => word.length >= 2 && !ignoredTerms.has(word)),
    ]),
  ];
}

function flatten(blocks: KnowledgeReadingBlock[]): TextBlock[] {
  const result: TextBlock[] = [];
  const ids = new Set<string>();
  let offset = 0;
  const walk = (items: KnowledgeReadingBlock[], depth: number) => {
    if (!Array.isArray(items) || depth > 200) {
      throw new KnowledgeError('KNOWLEDGE_SEARCH_SNAPSHOT_INVALID');
    }
    for (const block of items) {
      if (
        !block ||
        !/^b\d+$/.test(block.id) ||
        ids.has(block.id) ||
        typeof block.text !== 'string'
      ) {
        throw new KnowledgeError('KNOWLEDGE_SEARCH_SNAPSHOT_INVALID');
      }
      ids.add(block.id);
      if (block.text) {
        result.push({
          id: block.id,
          text: block.text,
          start: offset,
          end: offset + block.text.length,
        });
        offset += block.text.length + 1;
      }
      if (block.children) walk(block.children, depth + 1);
    }
  };
  walk(blocks, 0);
  return result;
}

function snippet(text: string, tokens: string[], maxChars: number): string {
  const lower = text.toLocaleLowerCase();
  const indexes = tokens.map((term) => lower.indexOf(term)).filter((index) => index >= 0);
  const first = indexes.length ? Math.min(...indexes) : 0;
  const start = Math.max(0, first - Math.floor(maxChars / 4));
  return text.slice(start, start + maxChars);
}

export class KnowledgeSearchService {
  readonly config: TKnowledgeSearchConfig;

  constructor(
    private readonly deps: {
      store: KnowledgeStore;
      semantic: KnowledgeSemanticSearch;
      ownerId: string;
      config?: Partial<TKnowledgeSearchConfig>;
    },
  ) {
    this.config = knowledgeSearchConfigSchema.parse(deps.config ?? {});
  }

  private async manifest(): Promise<Manifest> {
    const { models, sourceId } = this.deps.store;
    const session = await models.KnowledgeSource.db.startSession();
    try {
      let result: Manifest | undefined;
      await session.withTransaction(
        async () => {
          const source = await models.KnowledgeSource.findOne({ id: sourceId })
            .session(session)
            .lean();
          if (!source?.enabled || source.health !== 'healthy') {
            throw new KnowledgeError('KNOWLEDGE_SOURCE_UNAVAILABLE');
          }
          const nodes = await models.KnowledgeNode.find({
            sourceId,
            accessEpoch: source.accessEpoch,
            state: { $in: ['active', 'missing'] },
          })
            .sort({ id: 1 })
            .limit(this.config.maxNodes + 1)
            .session(session)
            .lean();
          const documents = await models.KnowledgeDocument.find({
            sourceId,
            accessEpoch: source.accessEpoch,
            requiresRevalidation: { $ne: true },
            status: { $nin: ['removed', 'inaccessible'] },
            activeRevisionId: { $exists: true },
          })
            .sort({ id: 1 })
            .limit(this.config.maxDocuments + 1)
            .session(session)
            .lean();
          if (nodes.length > this.config.maxNodes || documents.length > this.config.maxDocuments) {
            throw new KnowledgeError('KNOWLEDGE_SEARCH_BUDGET_EXCEEDED');
          }
          const revisions = await models.KnowledgeRevision.find({
            sourceId,
            id: { $in: documents.map((item) => item.activeRevisionId) },
            status: 'published',
            complete: true,
          })
            .session(session)
            .lean();
          const agent = await models.Agent.findOne({ id: source.agentId }).session(session).lean();
          if (!agent || agent.author?.toString() !== this.deps.ownerId) {
            throw new KnowledgeError('KNOWLEDGE_SEARCH_SCOPE_INVALID');
          }
          const byRevision = new Map(revisions.map((revision) => [revision.id, revision]));
          const fileIds = new Set<string>();
          const visibleIds = new Set(nodes.map((node) => node.documentId));
          let bytes = 0;
          const selected: SearchDocument[] = documents.map((document) => {
            const revision = byRevision.get(document.activeRevisionId!);
            if (
              !revision ||
              revision.documentId !== document.id ||
              !revision.nativeFileIds.length
            ) {
              throw new KnowledgeError('KNOWLEDGE_SEARCH_SCOPE_INVALID');
            }
            for (const fileId of revision.nativeFileIds) {
              if (fileIds.has(fileId)) throw new KnowledgeError('KNOWLEDGE_SEARCH_SCOPE_INVALID');
              fileIds.add(fileId);
            }
            bytes += Buffer.byteLength(revision.text);
            return { document, revision };
          });
          if (bytes > this.config.maxScanBytes)
            throw new KnowledgeError('KNOWLEDGE_SEARCH_BUDGET_EXCEEDED');
          const agentFiles = new Set(agent.tool_resources?.file_search?.file_ids ?? []);
          if (
            agentFiles.size !== fileIds.size ||
            [...fileIds].some((fileId) => !agentFiles.has(fileId))
          ) {
            throw new KnowledgeError('KNOWLEDGE_SEARCH_SCOPE_INVALID');
          }
          const visible = selected.filter(({ document }) => visibleIds.has(document.id));
          result = {
            nodes,
            documents: visible,
            snapshotId: `search_${digest([
              source.id,
              source.accessEpoch,
              source.agentId,
              this.deps.ownerId,
              visible.map(({ document, revision }) => [
                document.id,
                document.title,
                revision.id,
                revision.nativeFileIds,
              ]),
              nodes.map((node) => [node.id, node.parentId, node.documentId, node.title]),
            ])}`,
          };
        },
        { readConcern: { level: 'snapshot' }, writeConcern: { w: 'majority' } },
      );
      return result!;
    } finally {
      await session.endSession();
    }
  }

  private async blocks(
    revision: KnowledgeRevisionRecord,
    budget = { remaining: this.config.maxScanBytes },
  ): Promise<TextBlock[]> {
    const bytes = await this.deps.store.blobs.read(revision.blobKey);
    budget.remaining -= bytes.byteLength;
    if (budget.remaining < 0) throw new KnowledgeError('KNOWLEDGE_SEARCH_BUDGET_EXCEEDED');
    try {
      const blocks = flatten(JSON.parse(bytes.toString('utf8')).blocks);
      if (blocks.map((block) => block.text).join('\n') !== revision.text) {
        throw new KnowledgeError('KNOWLEDGE_SEARCH_SNAPSHOT_INVALID');
      }
      return blocks;
    } catch (error) {
      if (error instanceof KnowledgeError) throw error;
      throw new KnowledgeError('KNOWLEDGE_SEARCH_SNAPSHOT_INVALID');
    }
  }

  async validateHits(items: KnowledgeSearchReference[], snapshotId: string): Promise<boolean> {
    if (!/^search_[a-f0-9]{64}$/.test(snapshotId) || items.length > this.config.maxResults)
      return false;
    const state = await this.manifest();
    if (state.snapshotId !== snapshotId) return false;
    const budget = { remaining: this.config.maxScanBytes };
    const knownBlocks = new Map<string, Set<string>>();
    for (const item of items) {
      const document = state.documents.find(
        ({ document, revision }) =>
          document.id === item.documentId && revision.id === item.revisionId,
      );
      if (!document) return false;
      let blockIds = knownBlocks.get(document.revision.id);
      if (!blockIds) {
        blockIds = new Set((await this.blocks(document.revision, budget)).map((block) => block.id));
        knownBlocks.set(document.revision.id, blockIds);
      }
      if (!blockIds.has(item.blockId)) return false;
    }
    return (await this.manifest()).snapshotId === snapshotId;
  }

  async search(input: KnowledgeSearchRequest): Promise<KnowledgeSearchResponse> {
    if (
      !input ||
      typeof input !== 'object' ||
      Object.keys(input).some(
        (key) => !['query', 'mode', 'directoryId', 'limit', 'cursor'].includes(key),
      )
    ) {
      throw new KnowledgeError('KNOWLEDGE_SEARCH_QUERY_INVALID', 400);
    }
    const query = typeof input.query === 'string' ? input.query.trim() : '';
    const mode = input.mode ?? 'hybrid';
    const limit = input.limit ?? Math.min(this.config.defaultLimit, this.config.maxResults);
    if (
      !query ||
      query.length > this.config.maxQueryChars ||
      Array.from(query).some(
        (character) => character.charCodeAt(0) < 32 && !'\t\n\r'.includes(character),
      ) ||
      !['keyword', 'semantic', 'hybrid'].includes(mode) ||
      !Number.isInteger(limit) ||
      limit < 1 ||
      limit > this.config.maxResults ||
      (input.directoryId !== undefined &&
        (typeof input.directoryId !== 'string' || !nodeIdPattern.test(input.directoryId)))
    ) {
      throw new KnowledgeError('KNOWLEDGE_SEARCH_QUERY_INVALID', 400);
    }
    const started = Date.now();
    const signal = AbortSignal.timeout(this.config.requestTimeoutMs);
    const state = await this.manifest();
    const cursorKey = digest([query, mode, input.directoryId, state.snapshotId]);
    let offset = 0;
    if (input.cursor !== undefined) {
      try {
        if (
          typeof input.cursor !== 'string' ||
          input.cursor.length > 512 ||
          !/^[A-Za-z0-9_-]+$/.test(input.cursor)
        )
          throw new Error();
        const cursor = JSON.parse(Buffer.from(input.cursor, 'base64url').toString('utf8'));
        if (!Number.isSafeInteger(cursor.o) || cursor.o < 0 || cursor.o > this.config.maxScanBytes)
          throw new Error();
        if (cursor.k !== cursorKey)
          throw new KnowledgeError('KNOWLEDGE_SEARCH_SNAPSHOT_CHANGED', 409);
        offset = cursor.o;
      } catch (error) {
        if (error instanceof KnowledgeError) throw error;
        throw new KnowledgeError('KNOWLEDGE_SEARCH_QUERY_INVALID', 400);
      }
    }
    const byNode = new Map(state.nodes.map((node) => [node.id, node]));
    if (input.directoryId && !byNode.has(input.directoryId))
      throw new KnowledgeError('KNOWLEDGE_DIRECTORY_UNAVAILABLE', 404);
    const paths = new Map<string, string[]>();
    const scopedIds = new Set<string>();
    for (const node of state.nodes) {
      let current: KnowledgeNodeRecord | undefined = node;
      const ancestors = new Set<string>();
      const titles: string[] = [];
      while (current) {
        if (ancestors.has(current.id)) throw new KnowledgeError('KNOWLEDGE_SEARCH_SCOPE_INVALID');
        ancestors.add(current.id);
        titles.push(current.title);
        current = current.parentId ? byNode.get(current.parentId) : undefined;
      }
      if (!input.directoryId || ancestors.has(input.directoryId)) {
        scopedIds.add(node.documentId);
        paths.set(node.documentId, [...(paths.get(node.documentId) ?? []), ...titles]);
      }
    }
    const documents = state.documents.filter(({ document }) => scopedIds.has(document.id));
    const tokens = terms(query);
    const tokenCount = Math.max(1, tokens.length - 1);
    const hits = new Map<string, KnowledgeSearchHit>();
    const textBlocks = new Map<string, TextBlock[]>();
    const fileDocuments = new Map<string, SearchDocument>();
    const scanBudget = { remaining: this.config.maxScanBytes };
    const add = (
      record: SearchDocument,
      block: TextBlock,
      score: number,
      matchedBy: 'keyword' | 'semantic',
      matchTerms = tokens,
    ) => {
      const key = `${record.document.id}:${block.id}`;
      const prior = hits.get(key);
      if (prior) {
        prior.score = Math.max(prior.score, score);
        if (!prior.matchedBy.includes(matchedBy)) prior.matchedBy.push(matchedBy);
        return;
      }
      hits.set(key, {
        documentId: record.document.id,
        revisionId: record.revision.id,
        blockId: block.id,
        title: record.document.title,
        snippet: snippet(block.text, matchTerms, this.config.maxSnippetChars),
        href: `/knowledge/documents/${record.document.id}/revisions/${record.revision.id}#block-${block.id}`,
        score,
        matchedBy: [matchedBy],
        sourceUpdatedAt: record.revision.sourceUpdatedAt?.toISOString(),
      });
    };
    for (const record of documents) {
      if (signal.aborted) throw new KnowledgeError('KNOWLEDGE_SEARCH_UNAVAILABLE');
      const blocks = await this.blocks(record.revision, scanBudget);
      textBlocks.set(record.document.id, blocks);
      for (const fileId of record.revision.nativeFileIds) fileDocuments.set(fileId, record);
      if (mode === 'semantic') continue;
      const title = record.document.title.toLocaleLowerCase();
      const directory = (paths.get(record.document.id) ?? []).join('\n').toLocaleLowerCase();
      const titleMatches = tokens.filter((term) => title.includes(term)).length;
      const directoryMatches = tokens.filter((term) => directory.includes(term)).length;
      for (const block of blocks) {
        const lower = block.text.toLocaleLowerCase();
        const count = tokens.filter((term) => lower.includes(term)).length;
        if (count)
          add(
            record,
            block,
            Math.min(1, 0.2 + (0.65 * count) / tokenCount + (titleMatches ? 0.1 : 0)),
            'keyword',
          );
      }
      if ((titleMatches || directoryMatches) && blocks[0]) {
        add(
          record,
          blocks[0],
          Math.min(
            1,
            titleMatches
              ? 0.35 + (0.6 * titleMatches) / tokenCount
              : 0.2 + (0.35 * directoryMatches) / tokenCount,
          ),
          'keyword',
        );
      }
    }
    if (mode !== 'keyword' && fileDocuments.size) {
      let semantic: Awaited<ReturnType<KnowledgeSemanticSearch['query']>>;
      try {
        semantic = await this.deps.semantic.query({
          query,
          fileIds: [...fileDocuments.keys()],
          limit: this.config.maxSemanticHits,
          signal,
        });
      } catch (error) {
        if (error instanceof KnowledgeError) throw error;
        throw new KnowledgeError('KNOWLEDGE_SEARCH_UNAVAILABLE');
      }
      if (semantic.length > this.config.maxSemanticHits)
        throw new KnowledgeError('KNOWLEDGE_SEARCH_SCOPE_INVALID');
      for (const match of semantic) {
        const record = fileDocuments.get(match.fileId);
        if (
          !record ||
          typeof match.text !== 'string' ||
          !match.text.trim() ||
          !Number.isFinite(match.score) ||
          match.score < 0 ||
          match.score > 1
        )
          throw new KnowledgeError('KNOWLEDGE_SEARCH_SCOPE_INVALID');
        const text = match.text.trim();
        const start = record.revision.text.indexOf(text);
        if (start < 0) throw new KnowledgeError('KNOWLEDGE_SEARCH_SCOPE_INVALID');
        if (match.score < this.config.minSemanticScore) continue;
        const overlaps = (textBlocks.get(record.document.id) ?? []).filter(
          (block) => block.start < start + text.length && block.end > start,
        );
        if (!overlaps.length) throw new KnowledgeError('KNOWLEDGE_SEARCH_SCOPE_INVALID');
        for (const block of overlaps) add(record, block, match.score, 'semantic', [text]);
      }
    }
    const sorted = [...hits.values()].sort(
      (a, b) =>
        b.score - a.score ||
        a.documentId.localeCompare(b.documentId) ||
        a.blockId.localeCompare(b.blockId),
    );
    const items: KnowledgeSearchHit[] = [];
    let budget = this.config.maxContextChars;
    for (const hit of sorted.slice(offset, offset + limit)) {
      if (!budget) break;
      const selected = { ...hit, snippet: hit.snippet.slice(0, budget) };
      items.push(selected);
      budget -= selected.snippet.length;
    }
    if (Date.now() - started > this.config.requestTimeoutMs)
      throw new KnowledgeError('KNOWLEDGE_SEARCH_UNAVAILABLE');
    if ((await this.manifest()).snapshotId !== state.snapshotId)
      throw new KnowledgeError('KNOWLEDGE_SEARCH_SNAPSHOT_CHANGED', 409);
    const nextOffset = offset + items.length;
    return {
      query,
      mode,
      sourceStatus: 'ready',
      snapshotId: state.snapshotId,
      items,
      ...(nextOffset < sorted.length
        ? {
            nextCursor: Buffer.from(JSON.stringify({ o: nextOffset, k: cursorKey })).toString(
              'base64url',
            ),
          }
        : {}),
    };
  }
}

export function createKnowledgeSearchService(
  deps: ConstructorParameters<typeof KnowledgeSearchService>[0],
): KnowledgeSearchService {
  return new KnowledgeSearchService(deps);
}
