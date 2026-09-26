import { createNativeKnowledgeSearch } from './searchNative';

function fixture() {
  const getFile = jest.fn(async (fileId: string) => ({
    file_id: fileId,
    filename: 'fixture.txt',
    user: 'owner',
    bytes: 20,
    filepath: 'vectordb',
    type: 'text/plain',
    source: 'vectordb',
    context: 'knowledge',
    embedded: true,
    metadata: { embeddedEntities: ['agent_fixture'] },
  }));
  const http = jest.fn(
    async () =>
      new Response(
        JSON.stringify([
          [
            {
              page_content: '机器人课程',
              metadata: { file_id: 'file_a', user_id: 'agent_fixture' },
            },
            0.2,
          ],
        ]),
      ),
  );
  const service = createNativeKnowledgeSearch({
    agentId: 'agent_fixture',
    ownerId: 'owner',
    baseUrl: 'http://rag:8000',
    getFile,
    fetch: http,
    token: () => 'fixture-token',
  });
  return { getFile, http, service };
}

describe('native knowledge semantic search', () => {
  it('uses only server-owned entity and files and converts cosine distance', async () => {
    const { service, http } = fixture();
    const result = await service.query({ query: '机器人', fileIds: ['file_a'], limit: 8 });
    expect(result).toEqual([{ fileId: 'file_a', text: '机器人课程', score: 0.8 }]);
    const [url, options] = http.mock.calls[0] as unknown as [URL, RequestInit];
    expect(url.toString()).toBe('http://rag:8000/query_multiple');
    expect(JSON.parse(options.body as string)).toEqual({
      query: '机器人',
      file_ids: ['file_a'],
      entity_id: 'agent_fixture',
      k: 8,
    });
    expect(options.redirect).toBe('error');
  });

  it.each(['file_id', 'user_id'])(
    'rejects foreign %s even if RAG responds successfully',
    async (field) => {
      const { service, http } = fixture();
      http.mockResolvedValue(
        new Response(
          JSON.stringify([
            [
              {
                page_content: 'private',
                metadata: { file_id: 'file_a', user_id: 'agent_fixture', [field]: 'other' },
              },
              0.2,
            ],
          ]),
        ),
      );
      await expect(
        service.query({ query: '机器人', fileIds: ['file_a'], limit: 8 }),
      ).rejects.toMatchObject({ code: 'KNOWLEDGE_SEARCH_SCOPE_INVALID' });
    },
  );

  it('rejects native files owned by another account before requesting RAG', async () => {
    const { service, getFile, http } = fixture();
    getFile.mockResolvedValue({ ...(await getFile('file_a'))!, user: 'other' });
    await expect(
      service.query({ query: '机器人', fileIds: ['file_a'], limit: 8 }),
    ).rejects.toMatchObject({ code: 'KNOWLEDGE_SEARCH_SCOPE_INVALID' });
    expect(http).not.toHaveBeenCalled();
  });

  it('treats no hits as empty but does not hide service failure', async () => {
    const { service, http } = fixture();
    http.mockResolvedValueOnce(new Response('', { status: 404 }));
    expect(await service.query({ query: '不存在', fileIds: ['file_a'], limit: 8 })).toEqual([]);
    http.mockResolvedValueOnce(new Response('', { status: 503 }));
    await expect(
      service.query({ query: '机器人', fileIds: ['file_a'], limit: 8 }),
    ).rejects.toMatchObject({ code: 'KNOWLEDGE_SEARCH_UNAVAILABLE' });
  });

  it('bounds the response and rejects malformed scores', async () => {
    const { http, getFile } = fixture();
    const limited = createNativeKnowledgeSearch({
      agentId: 'agent_fixture',
      ownerId: 'owner',
      baseUrl: 'http://rag:8000',
      getFile,
      fetch: http,
      token: () => 'fixture',
      maxResponseBytes: 8,
    });
    await expect(
      limited.query({ query: '机器人', fileIds: ['file_a'], limit: 8 }),
    ).rejects.toMatchObject({ code: 'KNOWLEDGE_SEARCH_BUDGET_EXCEEDED' });
    const { service, http: otherHttp } = fixture();
    otherHttp.mockResolvedValue(
      new Response(
        JSON.stringify([
          [
            { page_content: '机器人', metadata: { file_id: 'file_a', user_id: 'agent_fixture' } },
            '0.1',
          ],
        ]),
      ),
    );
    await expect(
      service.query({ query: '机器人', fileIds: ['file_a'], limit: 8 }),
    ).rejects.toMatchObject({ code: 'KNOWLEDGE_SEARCH_SCOPE_INVALID' });
  });
});
