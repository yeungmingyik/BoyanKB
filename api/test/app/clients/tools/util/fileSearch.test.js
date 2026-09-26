const axios = require('axios');
const { ResourceType } = require('librechat-data-provider');

jest.mock('axios');
jest.mock('@librechat/api', () => {
  const { selectFileCitationSources, executeFileSearchQuery } =
    jest.requireActual('@librechat/api');
  return {
    generateShortLivedToken: jest.fn(),
    logAxiosError: jest.fn(),
    selectFileCitationSources,
    executeFileSearchQuery,
  };
});

jest.mock('@librechat/data-schemas', () => ({
  logger: {
    warn: jest.fn(),
    error: jest.fn(),
    debug: jest.fn(),
  },
}));

jest.mock('~/models', () => ({
  getFiles: jest.fn().mockResolvedValue([]),
}));

jest.mock('~/server/services/Files/permissions', () => ({
  filterFilesByAgentAccess: jest.fn((options) => Promise.resolve(options.files)),
}));

jest.mock('~/server/services/Knowledge', () => ({
  getKnowledgeService: jest.fn(),
}));

const { createFileSearchTool, primeFiles } = require('~/app/clients/tools/util/fileSearch');
const { generateShortLivedToken } = require('@librechat/api');

describe('fileSearch.js - active knowledge revisions', () => {
  const { getKnowledgeService } = require('~/server/services/Knowledge');
  const { logger } = require('@librechat/data-schemas');
  const { logAxiosError } = require('@librechat/api');
  const originalRagUrl = process.env.RAG_API_URL;
  const appConfig = { config: { knowledge: { enabled: true, sync: { enabled: true } } } };
  const files = [
    { file_id: 'current-file', filename: 'Current.txt', fromAgent: true },
    { file_id: 'old-file', filename: 'Old.txt', fromAgent: true },
  ];
  let activeFileIds;

  beforeEach(() => {
    jest.clearAllMocks();
    process.env.RAG_API_URL = 'http://rag.internal:8000';
    generateShortLivedToken.mockReturnValue('synthetic-jwt');
    activeFileIds = jest.fn().mockResolvedValue(['current-file']);
    getKnowledgeService.mockResolvedValue({ activeFileIds });
    axios.post.mockImplementation(async (_url, body) => ({
      data: [
        [
          {
            page_content: `Synthetic ${body.file_id} text`,
            metadata: { source: `/${body.file_id}.txt` },
          },
          0.2,
        ],
      ],
    }));
  });

  afterEach(() => {
    if (originalRagUrl === undefined) {
      delete process.env.RAG_API_URL;
    } else {
      process.env.RAG_API_URL = originalRagUrl;
    }
  });

  async function searchTool(config = appConfig) {
    return createFileSearchTool({
      userId: 'partner',
      entity_id: 'agent_knowledge',
      files,
      appConfig: config,
    });
  }

  it('filters retired file IDs before requesting search and keeps only current citations', async () => {
    const tool = await searchTool();
    const [content, artifact] = await tool.func({ query: 'synthetic query' });
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(axios.post.mock.calls[0][1]).toMatchObject({
      file_id: 'current-file',
      entity_id: 'agent_knowledge',
    });
    expect(content).toContain('Synthetic current-file text');
    expect(content).not.toContain('old-file');
    expect(artifact.file_search.sources.map((source) => source.fileId)).toEqual(['current-file']);
    expect(activeFileIds).toHaveBeenCalledTimes(2);
  });

  it('never calls RAG when none of the tool files remain active', async () => {
    activeFileIds.mockResolvedValue([]);
    const result = await (await searchTool()).func({ query: 'synthetic query' });
    expect(result[1]).toBeUndefined();
    expect(axios.post).not.toHaveBeenCalled();
    expect(generateShortLivedToken).not.toHaveBeenCalled();
  });

  it('discards the entire result and citations if its source is revoked during retrieval', async () => {
    activeFileIds.mockResolvedValueOnce(['current-file']).mockResolvedValueOnce([]);
    const result = await (await searchTool()).func({ query: 'synthetic query' });
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(result).toEqual(['KNOWLEDGE_SOURCE_CHANGED', undefined]);
    expect(JSON.stringify(result)).not.toContain('Synthetic current-file text');
  });

  it('rechecks publication state for every invocation of an existing tool', async () => {
    const tool = await searchTool();
    await tool.func({ query: 'first query' });
    activeFileIds.mockResolvedValue(['old-file']);
    await tool.func({ query: 'second query' });
    expect(axios.post.mock.calls.map(([, body]) => body.file_id)).toEqual([
      'current-file',
      'old-file',
    ]);
    expect(activeFileIds).toHaveBeenCalledTimes(4);
  });

  it.each([
    { config: { knowledge: { enabled: false, sync: { enabled: true } } } },
    { config: { knowledge: { enabled: true, sync: { enabled: false } } } },
    {},
  ])('preserves native file search when synchronization is disabled %p', async (config) => {
    const result = await (await searchTool(config)).func({ query: 'synthetic query' });
    expect(getKnowledgeService).not.toHaveBeenCalled();
    expect(axios.post.mock.calls.map(([, body]) => body.file_id)).toEqual([
      'current-file',
      'old-file',
    ]);
    expect(result[1].file_search.sources).toHaveLength(2);
  });

  it('fails closed with a static result if the pre-search source lookup fails', async () => {
    getKnowledgeService.mockRejectedValueOnce(new Error('synthetic_private_source_token'));
    const result = await (await searchTool()).func({ query: 'synthetic query' });
    expect(result).toEqual(['KNOWLEDGE_SOURCE_UNAVAILABLE', undefined]);
    expect(axios.post).not.toHaveBeenCalled();
  });

  it('never releases retrieved content when the post-search source check fails', async () => {
    activeFileIds
      .mockResolvedValueOnce(['current-file'])
      .mockRejectedValueOnce(new Error('synthetic_private_source_token'));
    const result = await (await searchTool()).func({ query: 'synthetic query' });
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(result).toEqual(['KNOWLEDGE_SOURCE_UNAVAILABLE', undefined]);
    expect(JSON.stringify(result)).not.toContain('Synthetic current-file text');
  });

  it('keeps the knowledge query and source text out of retrieval logs', async () => {
    await (await searchTool()).func({ query: 'synthetic private knowledge query' });
    expect(logger.debug.mock.calls).toEqual([['KNOWLEDGE_SEARCH_REQUEST']]);
    expect(logger.error).not.toHaveBeenCalled();
    expect(logAxiosError).not.toHaveBeenCalled();
  });

  it('logs a static error without upstream response bodies or credentials', async () => {
    axios.post.mockRejectedValueOnce({
      message: 'synthetic private upstream message',
      response: { data: 'synthetic private source text' },
      config: { headers: { Authorization: 'Bearer synthetic private credential' } },
    });
    const result = await (await searchTool()).func({ query: 'synthetic private knowledge query' });
    expect(result[1]).toBeUndefined();
    expect(JSON.stringify(result)).not.toContain('synthetic private');
    expect(logger.error.mock.calls).toEqual([['KNOWLEDGE_SEARCH_FAILED']]);
    expect(logger.debug.mock.calls).toEqual([['KNOWLEDGE_SEARCH_REQUEST']]);
    expect(logAxiosError).not.toHaveBeenCalled();
  });
});

describe('fileSearch.js - agent file authorization', () => {
  it('uses the permission resource type established by the calling route', async () => {
    const { getFiles } = require('~/models');
    const { filterFilesByAgentAccess } = require('~/server/services/Files/permissions');
    const files = [{ file_id: 'owner-file', filename: 'owner.pdf', user: 'agent-owner' }];
    getFiles.mockResolvedValueOnce(files);

    await primeFiles({
      req: { user: { id: 'remote-viewer', role: 'USER' } },
      agentId: 'agent-123',
      agentResourceType: ResourceType.REMOTE_AGENT,
      tool_resources: { file_search: { file_ids: ['owner-file'] } },
    });

    expect(filterFilesByAgentAccess).toHaveBeenCalledWith({
      files,
      userId: 'remote-viewer',
      role: 'USER',
      agentId: 'agent-123',
      resourceType: ResourceType.REMOTE_AGENT,
    });
  });
});

describe('fileSearch.js - tuple return validation', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    process.env.RAG_API_URL = 'http://localhost:8000';
  });

  describe('error cases should return tuple with undefined as second value', () => {
    it('should return tuple when no files provided', async () => {
      const fileSearchTool = await createFileSearchTool({
        userId: 'user1',
        files: [],
      });

      const result = await fileSearchTool.func({ query: 'test query' });

      expect(Array.isArray(result)).toBe(true);
      expect(result).toHaveLength(2);
      expect(result[0]).toBe('No files to search. Instruct the user to add files for the search.');
      expect(result[1]).toBeUndefined();
    });

    it.each(['', '   ', '\n\t', undefined])(
      'does not query any files when the search query is blank (%j)',
      async (query) => {
        const fileSearchTool = await createFileSearchTool({
          userId: 'user1',
          files: [
            { file_id: 'file-1', filename: 'one.pdf' },
            { file_id: 'file-2', filename: 'two.pdf' },
          ],
        });

        const result = await fileSearchTool.func({ query });

        expect(result).toEqual(['A non-empty query is required to search the files.', undefined]);
        expect(axios.post).not.toHaveBeenCalled();
        expect(generateShortLivedToken).not.toHaveBeenCalled();
      },
    );

    it('should return tuple when JWT token generation fails', async () => {
      generateShortLivedToken.mockReturnValue(null);

      const fileSearchTool = await createFileSearchTool({
        userId: 'user1',
        files: [{ file_id: 'file-1', filename: 'test.pdf' }],
      });

      const result = await fileSearchTool.func({ query: 'test query' });

      expect(Array.isArray(result)).toBe(true);
      expect(result).toHaveLength(2);
      expect(result[0]).toBe('There was an error authenticating the file search request.');
      expect(result[1]).toBeUndefined();
    });

    it('should return tuple when no valid results found', async () => {
      generateShortLivedToken.mockReturnValue('mock-jwt-token');
      axios.post.mockRejectedValue(new Error('API Error'));

      const fileSearchTool = await createFileSearchTool({
        userId: 'user1',
        files: [{ file_id: 'file-1', filename: 'test.pdf' }],
      });

      const result = await fileSearchTool.func({ query: 'test query' });

      expect(Array.isArray(result)).toBe(true);
      expect(result).toHaveLength(2);
      expect(result[0]).toBe('No results found or errors occurred while searching the files.');
      expect(result[1]).toBeUndefined();
    });
  });

  describe('success cases should return tuple with artifact object', () => {
    it.each([
      [0, [1]],
      [2, [3]],
      [undefined, []],
      [null, []],
      [-1, []],
      [1.5, []],
      ['2', []],
    ])('maps RAG page index %s to citation pages %j', async (page, pages) => {
      generateShortLivedToken.mockReturnValue('mock-jwt-token');
      axios.post.mockResolvedValue({
        data: [
          [{ page_content: 'Synthetic passage', metadata: { source: '/test.pdf', page } }, 0.2],
        ],
      });

      const fileSearchTool = await createFileSearchTool({
        userId: 'user1',
        files: [{ file_id: 'file-123', filename: 'test.pdf' }],
        fileCitations: true,
      });
      const [, artifact] = await fileSearchTool.func({ query: 'Synthetic page check' });
      const source = artifact.file_search.sources[0];

      expect(source.pages).toEqual(pages);
      expect(source.pageRelevance).toEqual(pages.length ? { [pages[0]]: expect.any(Number) } : {});
    });

    it('forwards valid query text unchanged', async () => {
      generateShortLivedToken.mockReturnValue('mock-jwt-token');
      axios.post.mockResolvedValue({ data: [] });
      const fileSearchTool = await createFileSearchTool({
        userId: 'user1',
        files: [{ file_id: 'file-1', filename: 'one.pdf' }],
      });

      await fileSearchTool.func({ query: '  find me  ' });

      expect(axios.post).toHaveBeenCalledWith(
        'http://localhost:8000/query',
        { file_id: 'file-1', query: '  find me  ', k: 5 },
        {
          headers: {
            Authorization: 'Bearer mock-jwt-token',
            'Content-Type': 'application/json',
          },
        },
      );
    });

    it('should return tuple with formatted results and sources artifact', async () => {
      generateShortLivedToken.mockReturnValue('mock-jwt-token');

      const mockApiResponse = {
        data: [
          [
            {
              page_content: 'This is test content from the document',
              metadata: { source: '/path/to/test.pdf', page: 0 },
            },
            0.2,
          ],
          [
            {
              page_content: 'Additional relevant content',
              metadata: { source: '/path/to/test.pdf', page: 1 },
            },
            0.35,
          ],
        ],
      };

      axios.post.mockResolvedValue(mockApiResponse);

      const fileSearchTool = await createFileSearchTool({
        userId: 'user1',
        files: [{ file_id: 'file-123', filename: 'test.pdf' }],
        entity_id: 'agent-456',
      });

      const result = await fileSearchTool.func({ query: 'test query' });

      expect(Array.isArray(result)).toBe(true);
      expect(result).toHaveLength(2);

      const [formattedString, artifact] = result;

      expect(typeof formattedString).toBe('string');
      expect(formattedString).toContain('File: test.pdf');
      expect(formattedString).toContain('Relevance:');
      expect(formattedString).toContain('This is test content from the document');
      expect(formattedString).toContain('Additional relevant content');

      expect(artifact).toBeDefined();
      expect(artifact).toHaveProperty('file_search');
      expect(artifact.file_search).toHaveProperty('sources');
      expect(artifact.file_search).toHaveProperty('fileCitations', false);
      expect(Array.isArray(artifact.file_search.sources)).toBe(true);
      expect(artifact.file_search.sources.length).toBe(2);

      const source = artifact.file_search.sources[0];
      expect(source).toMatchObject({
        type: 'file',
        fileId: 'file-123',
        fileName: 'test.pdf',
        content: expect.any(String),
        relevance: expect.any(Number),
        pages: [1],
        pageRelevance: { 1: expect.any(Number) },
      });
    });

    it('should include file citations in description when enabled', async () => {
      generateShortLivedToken.mockReturnValue('mock-jwt-token');

      const mockApiResponse = {
        data: [
          [
            {
              page_content: 'Content with citations',
              metadata: { source: '/path/to/doc.pdf', page: 3 },
            },
            0.15,
          ],
        ],
      };

      axios.post.mockResolvedValue(mockApiResponse);

      const fileSearchTool = await createFileSearchTool({
        userId: 'user1',
        files: [{ file_id: 'file-789', filename: 'doc.pdf' }],
        fileCitations: true,
      });

      const result = await fileSearchTool.func({ query: 'test query' });

      expect(Array.isArray(result)).toBe(true);
      expect(result).toHaveLength(2);

      const [formattedString, artifact] = result;

      expect(formattedString).toContain('Anchor:');
      expect(formattedString).toContain('\\ue202turn0file0');
      expect(artifact.file_search.fileCitations).toBe(true);
    });

    it('keeps a successful file result when another file search fails', async () => {
      generateShortLivedToken.mockReturnValue('mock-jwt-token');
      axios.post.mockRejectedValueOnce(new Error('file unavailable')).mockResolvedValueOnce({
        data: [[{ page_content: 'Found passage', metadata: { source: '/good.pdf' } }, 0.2]],
      });
      const fileSearchTool = await createFileSearchTool({
        userId: 'user1',
        files: [
          { file_id: 'missing', filename: 'missing.pdf' },
          { file_id: 'good', filename: 'good.pdf' },
        ],
      });

      const [content, artifact] = await fileSearchTool.func({ query: 'lookup' });

      expect(axios.post).toHaveBeenCalledTimes(2);
      expect(content).toContain('Found passage');
      expect(artifact.file_search.sources).toHaveLength(1);
      expect(artifact.file_search.sources[0].fileId).toBe('good');
    });

    it('should handle multiple files correctly', async () => {
      generateShortLivedToken.mockReturnValue('mock-jwt-token');

      const mockResponse1 = {
        data: [
          [
            {
              page_content: 'Content from file 1',
              metadata: { source: '/path/to/file1.pdf', page: 1 },
            },
            0.25,
          ],
        ],
      };

      const mockResponse2 = {
        data: [
          [
            {
              page_content: 'Content from file 2',
              metadata: { source: '/path/to/file2.pdf', page: 1 },
            },
            0.15,
          ],
        ],
      };

      axios.post.mockResolvedValueOnce(mockResponse1).mockResolvedValueOnce(mockResponse2);

      const fileSearchTool = await createFileSearchTool({
        userId: 'user1',
        files: [
          { file_id: 'file-1', filename: 'file1.pdf' },
          { file_id: 'file-2', filename: 'file2.pdf' },
        ],
      });

      const result = await fileSearchTool.func({ query: 'test query' });

      expect(Array.isArray(result)).toBe(true);
      expect(result).toHaveLength(2);

      const [formattedString, artifact] = result;

      expect(formattedString).toContain('file1.pdf');
      expect(formattedString).toContain('file2.pdf');
      expect(artifact.file_search.sources).toHaveLength(2);
      // Results are sorted by distance (ascending), so file-2 (0.15) comes before file-1 (0.25)
      expect(artifact.file_search.sources[0].fileId).toBe('file-2');
      expect(artifact.file_search.sources[1].fileId).toBe('file-1');
    });
  });
});

describe('entity_id scoping by file origin', () => {
  const ORIGINAL_RAG_API_URL = process.env.RAG_API_URL;

  beforeEach(() => {
    jest.clearAllMocks();
    process.env.RAG_API_URL = 'http://localhost:8000';
    generateShortLivedToken.mockReturnValue('mock-jwt-token');
    axios.post.mockResolvedValue({ data: [] });
  });

  afterEach(() => {
    if (ORIGINAL_RAG_API_URL === undefined) {
      delete process.env.RAG_API_URL;
    } else {
      process.env.RAG_API_URL = ORIGINAL_RAG_API_URL;
    }
  });

  function bodiesSent() {
    return axios.post.mock.calls
      .filter(([url]) => String(url).endsWith('/query'))
      .map(([, body]) => body);
  }

  it('sends entity_id only for agent knowledge-base files', async () => {
    const tool = await createFileSearchTool({
      userId: 'user1',
      entity_id: 'agent_123',
      files: [
        { file_id: 'kb-1', filename: 'kb.pdf', fromAgent: true },
        { file_id: 'user-1', filename: 'attachment.txt', fromAgent: false },
      ],
    });
    await tool.func({ query: 'q' });

    const bodies = bodiesSent();
    expect(bodies.find((b) => b.file_id === 'kb-1').entity_id).toBe('agent_123');
    expect(bodies.find((b) => b.file_id === 'user-1').entity_id).toBeUndefined();
  });

  it('omits entity_id when fromAgent is not set (safe default)', async () => {
    const tool = await createFileSearchTool({
      userId: 'user1',
      entity_id: 'agent_123',
      files: [{ file_id: 'legacy-1', filename: 'legacy.pdf' }],
    });
    await tool.func({ query: 'q' });
    expect(bodiesSent()[0].entity_id).toBeUndefined();
  });

  it('sends no entity_id when none is provided', async () => {
    const tool = await createFileSearchTool({
      userId: 'user1',
      files: [{ file_id: 'f1', filename: 'a.txt', fromAgent: true }],
    });
    await tool.func({ query: 'q' });
    expect(bodiesSent()[0].entity_id).toBeUndefined();
  });
});
