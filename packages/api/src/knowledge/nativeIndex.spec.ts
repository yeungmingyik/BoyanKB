import type { NativeKnowledgeFile } from './nativeIndex';
import { createNativeKnowledgeIndexer, KnowledgeIndexError } from './nativeIndex';
import { generateShortLivedToken } from '~/crypto/jwt';

jest.mock('~/crypto/jwt', () => ({
  generateShortLivedToken: jest.fn(() => 'synthetic_owner_token'),
}));

const input = {
  documentId: 'document_internal',
  revisionId: 'revision_internal',
  title: 'Synthetic title',
  text: 'Synthetic searchable text',
  idempotencyKey: 'synthetic_content_hash',
};

function stored(overrides: Partial<NativeKnowledgeFile> = {}): NativeKnowledgeFile {
  return {
    file_id: 'existing_file',
    filename: 'existing.txt',
    user: 'owner_1',
    bytes: 10,
    filepath: 'vectordb',
    type: 'text/plain',
    source: 'vectordb',
    context: 'knowledge',
    embedded: true,
    metadata: { embeddedEntities: ['agent_knowledge'] },
    ...overrides,
  };
}

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

function fixture() {
  const findFile = jest.fn<Promise<NativeKnowledgeFile | null>, [string]>().mockResolvedValue(null);
  const getFile = jest
    .fn<Promise<NativeKnowledgeFile | null>, [string]>()
    .mockResolvedValue(stored());
  const saveFile = jest.fn(async (_file: NativeKnowledgeFile) => {});
  const deleteFile = jest.fn(async (_fileId: string) => {});
  const fetcher = jest.fn(async (url: URL, init?: RequestInit): Promise<Response> => {
    if (url.pathname === '/embed') {
      return json({ status: true, known_type: true });
    }
    if (init?.method === 'DELETE') {
      return json({ success: true });
    }
    return json([
      {
        page_content: 'Synthetic text',
        metadata: {
          file_id: url.searchParams.get('ids'),
          user_id: url.searchParams.get('entity_id'),
        },
      },
    ]);
  });
  const dependencies = {
    agentId: 'agent_knowledge',
    ownerId: 'owner_1',
    baseUrl: 'http://rag.internal:8000',
    version: 'index-1',
    findFile,
    getFile,
    saveFile,
    deleteFile,
    fetch: fetcher,
  };
  return {
    ...dependencies,
    fetcher,
    indexer: createNativeKnowledgeIndexer(dependencies),
    dependencies,
  };
}

describe('native knowledge indexer', () => {
  it('indexes under the Agent entity using the owner identity and saves only after vector verification', async () => {
    const { indexer, fetcher, saveFile } = fixture();
    const result = await indexer.index(input);
    expect(generateShortLivedToken).toHaveBeenCalledWith('owner_1');
    expect(result.fileIds).toHaveLength(1);
    const fileId = result.fileIds[0];
    const embed = fetcher.mock.calls[0];
    expect(embed[0].pathname).toBe('/embed');
    expect(embed[1]).toMatchObject({
      method: 'POST',
      redirect: 'error',
      headers: { Authorization: 'Bearer synthetic_owner_token' },
    });
    const form = embed[1]?.body as FormData;
    expect(form.get('entity_id')).toBe('agent_knowledge');
    expect(form.get('file_id')).toBe(fileId);
    expect(await (form.get('file') as Blob).text()).toBe(input.text);
    const check = fetcher.mock.calls[1][0];
    expect(check.searchParams.get('ids')).toBe(fileId);
    expect(check.searchParams.get('entity_id')).toBe('agent_knowledge');
    expect(saveFile).toHaveBeenCalledWith(
      expect.objectContaining({
        file_id: fileId,
        user: 'owner_1',
        embedded: true,
        context: 'knowledge',
        metadata: { embeddedEntities: ['agent_knowledge'] },
      }),
    );
    expect(fetcher.mock.invocationCallOrder[1]).toBeLessThan(saveFile.mock.invocationCallOrder[0]);
    expect(JSON.stringify(fetcher.mock.calls.map(([url]) => url.href))).not.toContain(
      'synthetic_owner_token',
    );
    expect(JSON.stringify(saveFile.mock.calls)).not.toContain('synthetic_owner_token');
  });

  it('reuses only completed and verified files', async () => {
    const { indexer, findFile, fetcher, saveFile } = fixture();
    findFile.mockResolvedValue(stored());
    expect((await indexer.index(input)).fileIds).toEqual(['existing_file']);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0].pathname).toBe('/documents');
    expect(saveFile).not.toHaveBeenCalled();
  });

  it('does not reuse an incomplete native File even when chunks exist', async () => {
    const { indexer, findFile, getFile, fetcher, saveFile } = fixture();
    findFile.mockResolvedValue(stored({ embedded: false }));
    getFile.mockResolvedValue(stored({ embedded: false }));
    const result = await indexer.index(input);
    expect(result.fileIds).not.toContain('existing_file');
    expect(fetcher.mock.calls[0][0].pathname).toBe('/embed');
    expect(saveFile).toHaveBeenCalledTimes(1);
  });

  it('keeps its idempotency filename stable and separate across Agent and index versions', async () => {
    const { dependencies, indexer, findFile } = fixture();
    await indexer.index(input);
    const first = findFile.mock.calls[0][0];
    await indexer.index({ ...input, title: 'Renamed title' });
    expect(findFile.mock.calls[1][0]).toBe(first);
    await createNativeKnowledgeIndexer({ ...dependencies, agentId: 'agent_other' }).index(input);
    expect(findFile.mock.calls[2][0]).not.toBe(first);
    await createNativeKnowledgeIndexer({ ...dependencies, version: 'index-2' }).index(input);
    expect(findFile.mock.calls[3][0]).not.toBe(first);
    expect(first).toMatch(/^[a-f0-9]{64}\.txt$/);
  });

  it.each([
    { user: 'another_owner' },
    { file_id: 'another_file' },
    { context: 'message' },
    { metadata: { embeddedEntities: ['another_agent'] } },
    { metadata: { embeddedEntities: ['agent_knowledge', 'another_agent'] } },
    { embedded: false },
  ])('rejects a native File with incorrect ownership or state %p', async (override) => {
    const { indexer, getFile, fetcher } = fixture();
    getFile.mockResolvedValue(stored(override));
    expect(await indexer.verify(['existing_file'])).toBe(false);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each([
    { file_id: 'another_file', user_id: 'agent_knowledge' },
    { file_id: 'existing_file', user_id: 'another_agent' },
    { file_id: 'existing_file', user_id: 'owner_1' },
  ])('rejects vectors outside the requested file and Agent scope %p', async (metadata) => {
    const { indexer, fetcher } = fixture();
    fetcher.mockResolvedValue(json([{ page_content: 'Synthetic', metadata }]));
    expect(await indexer.verify(['existing_file'])).toBe(false);
  });

  it('does not save a native File after embedding or vector verification fails', async () => {
    const failed = fixture();
    failed.fetcher.mockResolvedValueOnce(json({ message: 'private_token' }, 500));
    await expect(failed.indexer.index(input)).rejects.toMatchObject({
      code: 'KNOWLEDGE_INDEX_FAILED',
    });
    expect(failed.saveFile).not.toHaveBeenCalled();
    const unverified = fixture();
    unverified.fetcher
      .mockResolvedValueOnce(json({ status: true, known_type: true }))
      .mockResolvedValueOnce(json([]));
    await expect(unverified.indexer.index(input)).rejects.toMatchObject({
      code: 'KNOWLEDGE_INDEX_VERIFY_FAILED',
    });
    expect(unverified.saveFile).not.toHaveBeenCalled();
  });

  it('deletes only the scoped entity and file before deleting its native record', async () => {
    const { indexer, fetcher, deleteFile } = fixture();
    await indexer.remove(['existing_file']);
    const [url, init] = fetcher.mock.calls[0];
    expect(url.pathname).toBe('/documents');
    expect(url.searchParams.get('entity_id')).toBe('agent_knowledge');
    expect(init).toMatchObject({ method: 'DELETE', body: JSON.stringify(['existing_file']) });
    expect(deleteFile).toHaveBeenCalledWith('existing_file');
    expect(fetcher.mock.invocationCallOrder[0]).toBeLessThan(
      deleteFile.mock.invocationCallOrder[0],
    );
  });

  it.each([
    { user: 'another_owner' },
    { file_id: 'another_file' },
    { context: 'message' },
    { metadata: { embeddedEntities: ['another_agent'] } },
    { metadata: { embeddedEntities: ['agent_knowledge', 'another_agent'] } },
  ])('never deletes records outside its knowledge scope %p', async (override) => {
    const { indexer, getFile, fetcher, deleteFile } = fixture();
    getFile.mockResolvedValue(stored(override));
    await indexer.remove(['existing_file']);
    expect(fetcher).not.toHaveBeenCalled();
    expect(deleteFile).not.toHaveBeenCalled();
  });

  it('retains the native file if vector deletion fails', async () => {
    const { indexer, fetcher, deleteFile } = fixture();
    fetcher.mockResolvedValue(json({}, 500));
    await expect(indexer.remove(['existing_file'])).rejects.toMatchObject({
      code: 'KNOWLEDGE_INDEX_DELETE_FAILED',
    });
    expect(deleteFile).not.toHaveBeenCalled();
  });

  it.each([
    'invalid private_token',
    'file:///private_token',
    'https://user:private_token@rag.internal',
  ])('sanitizes invalid RAG configuration %s', (baseUrl) => {
    const { dependencies } = fixture();
    expect(() => createNativeKnowledgeIndexer({ ...dependencies, baseUrl })).toThrow(
      KnowledgeIndexError,
    );
    expect(() => createNativeKnowledgeIndexer({ ...dependencies, baseUrl })).toThrow(
      'KNOWLEDGE_INDEX_CONFIG_INVALID',
    );
  });

  it('sanitizes network and malformed JSON errors without saving incomplete files', async () => {
    const network = fixture();
    network.fetcher.mockRejectedValue(new Error('private_token'));
    const networkError = await network.indexer.index(input).catch((error: unknown) => error);
    expect(networkError).toBeInstanceOf(KnowledgeIndexError);
    expect(String(networkError)).not.toContain('private_token');
    const malformed = fixture();
    malformed.fetcher.mockResolvedValue(new Response('private_token', { status: 200 }));
    const malformedError = await malformed.indexer.index(input).catch((error: unknown) => error);
    expect(malformedError).toBeInstanceOf(KnowledgeIndexError);
    expect(String(malformedError)).not.toContain('private_token');
    expect(malformed.saveFile).not.toHaveBeenCalled();
  });

  it('rejects null embedding and chunk records with stable outcomes', async () => {
    const embedded = fixture();
    embedded.fetcher.mockResolvedValue(json(null));
    await expect(embedded.indexer.index(input)).rejects.toMatchObject({
      code: 'KNOWLEDGE_INDEX_VERIFY_FAILED',
    });
    expect(embedded.saveFile).not.toHaveBeenCalled();
    const chunks = fixture();
    chunks.fetcher.mockResolvedValue(json([null]));
    await expect(chunks.indexer.verify(['existing_file'])).resolves.toBe(false);
  });

  it('rejects empty content without calling the RAG service', async () => {
    const { indexer, fetcher } = fixture();
    await expect(indexer.index({ ...input, text: ' \n\t' })).rejects.toMatchObject({
      code: 'KNOWLEDGE_INDEX_EMPTY',
    });
    expect(fetcher).not.toHaveBeenCalled();
  });
});
