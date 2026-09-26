import { createHash, randomUUID } from 'node:crypto';
import type { KnowledgeIndexer } from './sync';
import { generateShortLivedToken } from '~/crypto/jwt';

export type NativeKnowledgeFile = {
  file_id: string;
  filename: string;
  user: string;
  bytes: number;
  filepath: string;
  type: string;
  source: string;
  context: string;
  embedded: boolean;
  metadata: { embeddedEntities: string[] };
};

type Dependencies = {
  agentId: string;
  ownerId: string;
  baseUrl: string;
  version: string;
  findFile: (filename: string) => Promise<NativeKnowledgeFile | null>;
  getFile: (fileId: string) => Promise<NativeKnowledgeFile | null>;
  saveFile: (file: NativeKnowledgeFile) => Promise<unknown>;
  deleteFile: (fileId: string) => Promise<unknown>;
  fetch?: (input: URL, init?: RequestInit) => Promise<Response>;
  token?: () => string;
  timeoutMs?: number;
};

export class KnowledgeIndexError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = 'KnowledgeIndexError';
  }
}

export function createNativeKnowledgeIndexer(
  deps: Dependencies,
): KnowledgeIndexer & { remove(fileIds: string[]): Promise<void> } {
  const http = deps.fetch ?? fetch;
  let base: URL;
  try {
    base = new URL(deps.baseUrl);
  } catch {
    throw new KnowledgeIndexError('KNOWLEDGE_INDEX_CONFIG_INVALID');
  }
  if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password) {
    throw new KnowledgeIndexError('KNOWLEDGE_INDEX_CONFIG_INVALID');
  }
  const timeoutMs = deps.timeoutMs ?? 120000;

  function owned(file: NativeKnowledgeFile | null, fileId: string): file is NativeKnowledgeFile {
    return Boolean(
      file &&
        file.file_id === fileId &&
        file.user?.toString() === deps.ownerId &&
        file.context === 'knowledge' &&
        file.metadata?.embeddedEntities?.length === 1 &&
        file.metadata.embeddedEntities[0] === deps.agentId,
    );
  }

  async function json(response: Response) {
    try {
      return await response.json();
    } catch {
      throw new KnowledgeIndexError('KNOWLEDGE_INDEX_RESPONSE_INVALID');
    }
  }

  async function send(path: string, init?: RequestInit) {
    try {
      return await http(new URL(path, base), {
        ...init,
        headers: {
          Authorization: `Bearer ${deps.token?.() ?? generateShortLivedToken(deps.ownerId)}`,
          ...init?.headers,
        },
        redirect: 'error',
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch {
      throw new KnowledgeIndexError('KNOWLEDGE_INDEX_UNAVAILABLE');
    }
  }

  async function hasVectors(fileId: string): Promise<boolean> {
    const query = new URLSearchParams({ ids: fileId, entity_id: deps.agentId });
    const response = await send(`/documents?${query}`);
    if (response.status === 404) {
      return false;
    }
    if (!response.ok) {
      throw new KnowledgeIndexError('KNOWLEDGE_INDEX_UNAVAILABLE');
    }
    const rows = await json(response);
    return (
      Array.isArray(rows) &&
      rows.length > 0 &&
      rows.every(
        (row) =>
          typeof row?.page_content === 'string' &&
          row.metadata?.file_id === fileId &&
          row.metadata?.user_id === deps.agentId,
      )
    );
  }

  async function verify(fileIds: string[]) {
    if (fileIds.length === 0) {
      return false;
    }
    for (const fileId of fileIds) {
      const file = await deps.getFile(fileId);
      if (!owned(file, fileId) || !file.embedded || !(await hasVectors(fileId))) {
        return false;
      }
    }
    return true;
  }

  async function remove(fileIds: string[]) {
    for (const fileId of fileIds) {
      const file = await deps.getFile(fileId);
      if (!owned(file, fileId)) {
        continue;
      }
      const query = new URLSearchParams({ entity_id: deps.agentId });
      const response = await send(`/documents?${query}`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify([fileId]),
      });
      if (!response.ok && response.status !== 404) {
        throw new KnowledgeIndexError('KNOWLEDGE_INDEX_DELETE_FAILED');
      }
      await deps.deleteFile(fileId);
    }
  }

  async function index(input: {
    documentId: string;
    revisionId: string;
    title: string;
    text: string;
    idempotencyKey: string;
  }) {
    const digest = createHash('sha256')
      .update(JSON.stringify([deps.agentId, deps.version, input.idempotencyKey]))
      .digest('hex');
    const filename = `${digest}.txt`;
    const existing = await deps.findFile(filename);
    if (existing && (await verify([existing.file_id]))) {
      return { fileIds: [existing.file_id], indexedAt: new Date() };
    }
    const text = input.text.trim();
    if (!text) {
      throw new KnowledgeIndexError('KNOWLEDGE_INDEX_EMPTY');
    }
    const fileId = randomUUID();
    const form = new FormData();
    form.append('file_id', fileId);
    form.append('entity_id', deps.agentId);
    form.append('file', new Blob([text], { type: 'text/plain' }), `${fileId}.txt`);
    const response = await send('/embed', { method: 'POST', body: form });
    if (!response.ok) {
      throw new KnowledgeIndexError('KNOWLEDGE_INDEX_FAILED');
    }
    const result = await json(response);
    if (result?.status !== true || !result.known_type || !(await hasVectors(fileId))) {
      throw new KnowledgeIndexError('KNOWLEDGE_INDEX_VERIFY_FAILED');
    }
    await deps.saveFile({
      file_id: fileId,
      filename,
      user: deps.ownerId,
      bytes: Buffer.byteLength(text),
      filepath: 'vectordb',
      source: 'vectordb',
      type: 'text/plain',
      context: 'knowledge',
      embedded: true,
      metadata: { embeddedEntities: [deps.agentId] },
    });
    return { fileIds: [fileId], indexedAt: new Date() };
  }

  return { version: deps.version, index, verify, remove };
}
