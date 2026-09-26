import type { NativeKnowledgeFile } from './nativeIndex';
import type { KnowledgeSemanticSearch } from './search';
import { generateShortLivedToken } from '~/crypto/jwt';
import { KnowledgeError } from './store';

export function createNativeKnowledgeSearch(deps: {
  agentId: string;
  ownerId: string;
  baseUrl: string;
  getFile: (fileId: string) => Promise<NativeKnowledgeFile | null>;
  fetch?: (url: URL, init?: RequestInit) => Promise<Response>;
  token?: () => string;
  timeoutMs?: number;
  maxResponseBytes?: number;
}): KnowledgeSemanticSearch {
  let base: URL;
  try {
    base = new URL(deps.baseUrl);
    if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password) {
      throw new Error();
    }
  } catch {
    throw new KnowledgeError('KNOWLEDGE_SEARCH_CONFIG_INVALID');
  }
  return {
    async query({ query, fileIds, limit, signal }) {
      if (!fileIds.length) {
        return [];
      }
      for (const fileId of fileIds) {
        const file = await deps.getFile(fileId);
        if (
          !file ||
          file.file_id !== fileId ||
          file.user?.toString() !== deps.ownerId ||
          !file.embedded ||
          file.context !== 'knowledge' ||
          file.metadata?.embeddedEntities?.length !== 1 ||
          file.metadata.embeddedEntities[0] !== deps.agentId
        ) {
          throw new KnowledgeError('KNOWLEDGE_SEARCH_SCOPE_INVALID');
        }
      }
      try {
        const response = await (deps.fetch ?? fetch)(new URL('/query_multiple', base), {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${deps.token?.() ?? generateShortLivedToken(deps.ownerId)}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ query, file_ids: fileIds, entity_id: deps.agentId, k: limit }),
          redirect: 'error',
          signal: signal ?? AbortSignal.timeout(deps.timeoutMs ?? 15000),
        });
        if (response.status === 404) {
          return [];
        }
        if (!response.ok || !response.body) {
          throw new KnowledgeError('KNOWLEDGE_SEARCH_UNAVAILABLE');
        }
        const reader = response.body.getReader();
        const buffers: Uint8Array[] = [];
        let bytes = 0;
        try {
          for (;;) {
            const part = await reader.read();
            if (part.done) break;
            bytes += part.value.byteLength;
            if (bytes > (deps.maxResponseBytes ?? 2097152)) {
              await reader.cancel();
              throw new KnowledgeError('KNOWLEDGE_SEARCH_BUDGET_EXCEEDED');
            }
            buffers.push(part.value);
          }
        } finally {
          reader.releaseLock();
        }
        const rows: unknown = JSON.parse(Buffer.concat(buffers).toString('utf8'));
        if (!Array.isArray(rows) || rows.length > limit) {
          throw new KnowledgeError('KNOWLEDGE_SEARCH_SCOPE_INVALID');
        }
        const allowed = new Set(fileIds);
        return rows.map((row) => {
          if (
            !Array.isArray(row) ||
            row.length !== 2 ||
            typeof row[0]?.page_content !== 'string' ||
            !row[0].page_content.trim() ||
            !allowed.has(row[0].metadata?.file_id) ||
            row[0].metadata?.user_id !== deps.agentId ||
            typeof row[1] !== 'number' ||
            !Number.isFinite(row[1]) ||
            row[1] < -0.001 ||
            row[1] > 2.001
          ) {
            throw new KnowledgeError('KNOWLEDGE_SEARCH_SCOPE_INVALID');
          }
          return {
            fileId: row[0].metadata.file_id as string,
            text: row[0].page_content as string,
            score: Math.max(0, Math.min(1, 1 - row[1])),
          };
        });
      } catch (error) {
        if (error instanceof KnowledgeError) throw error;
        throw new KnowledgeError('KNOWLEDGE_SEARCH_UNAVAILABLE');
      }
    },
  };
}
