import type { FeishuClientOptions } from './feishu';
import { FeishuClient, FeishuError } from './feishu';

const syntheticNode = {
  space_id: 'space_1',
  node_token: 'leaf_1',
  obj_token: 'document_1',
  obj_type: 'docx',
  title: 'Synthetic document',
  has_child: false,
};

function json(value: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'content-type': 'application/json', ...headers },
  });
}

function fixture(
  respond: (url: URL, init: RequestInit) => Promise<Response> | Response,
  options: Partial<FeishuClientOptions> = {},
) {
  const fetcher = jest.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input));
    if (url.pathname.endsWith('/tenant_access_token/internal')) {
      return json({ code: 0, tenant_access_token: 'synthetic_token', expire: 7200 });
    }
    return respond(url, init ?? {});
  });
  const sleep = jest.fn(async () => {});
  const client = new FeishuClient({
    appId: 'synthetic_app',
    appSecret: 'synthetic_secret',
    fetch: fetcher,
    random: () => 0,
    sleep,
    ...options,
  });
  return { client, fetcher, sleep };
}

describe('Feishu read client', () => {
  it('resolves a leaf entry to its whole space through the fixed API origin', async () => {
    const { client, fetcher } = fixture(() => json({ code: 0, data: { node: syntheticNode } }));
    expect(
      await client.resolveSpace('https://example.feishu.cn/wiki/leaf_1?from=copy', 'space_1'),
    ).toEqual({ spaceId: 'space_1', node: syntheticNode });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(
      fetcher.mock.calls.every(([url]) =>
        String(url).startsWith('https://open.feishu.cn/open-apis/'),
      ),
    ).toBe(true);
    expect(fetcher.mock.calls[1][1]).toMatchObject({
      redirect: 'manual',
      headers: { Authorization: 'Bearer synthetic_token' },
    });
  });

  it.each([
    'http://example.feishu.cn/wiki/leaf_1',
    'https://example.feishu.cn.evil.test/wiki/leaf_1',
    'https://user:password@example.feishu.cn/wiki/leaf_1',
    'https://example.feishu.cn:8443/wiki/leaf_1',
    'https://127.0.0.1/wiki/leaf_1',
    'https://example.feishu.cn/docx/document_1',
  ])('rejects invalid source %s without making any request', async (url) => {
    const { client, fetcher } = fixture(() => json({}));
    await expect(client.resolveSpace(url)).rejects.toMatchObject({ kind: 'invalid_response' });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('rejects a different resolved space', async () => {
    const { client } = fixture(() => json({ code: 0, data: { node: syntheticNode } }));
    await expect(
      client.resolveSpace('https://example.feishu.cn/wiki/leaf_1', 'another_space'),
    ).rejects.toMatchObject({ kind: 'source_denied', scope: 'source' });
  });

  it('shares one token request between concurrent readers and refreshes before expiry', async () => {
    let now = 0;
    const { client, fetcher } = fixture(() => json({ code: 0, data: { node: syntheticNode } }), {
      now: () => now,
    });
    await Promise.all([
      client.getNode('leaf_1'),
      client.getNode('leaf_1'),
      client.getNode('leaf_1'),
    ]);
    expect(
      fetcher.mock.calls.filter(([url]) => String(url).includes('tenant_access_token')),
    ).toHaveLength(1);
    now = 7_150_000;
    await client.getNode('leaf_1');
    expect(
      fetcher.mock.calls.filter(([url]) => String(url).includes('tenant_access_token')),
    ).toHaveLength(2);
  });

  it('returns an empty intermediate node page with its continuation token', async () => {
    const { client, fetcher } = fixture(() =>
      json({ code: 0, data: { items: [], has_more: true, page_token: 'continue' } }),
    );
    expect(await client.listNodes('space_1')).toEqual({
      items: [],
      hasMore: true,
      pageToken: 'continue',
    });
    expect(String(fetcher.mock.calls[1][0])).not.toContain('parent_node_token');
  });

  it.each([
    { items: [], has_more: true },
    { items: [], has_more: true, page_token: 'same' },
    { items: [], has_more: 'true' },
  ])('rejects an invalid continuation page', async (data) => {
    const { client } = fixture(() => json({ code: 0, data }));
    await expect(client.listNodes('space_1', undefined, 'same')).rejects.toMatchObject({
      kind: 'invalid_response',
    });
  });

  it('reads blocks through empty pages without changing the latest revision parameter', async () => {
    const block = { block_id: 'block_1', block_type: 2, text: { elements: [] } };
    const { client, fetcher } = fixture((url) =>
      json({
        code: 0,
        data: url.searchParams.has('page_token')
          ? { items: [block], has_more: false }
          : { items: [], has_more: true, page_token: 'next/+=token' },
      }),
    );
    expect(await client.getBlocks('document_1')).toEqual([block]);
    const second = new URL(String(fetcher.mock.calls[2][0]));
    expect(second.searchParams.get('page_token')).toBe('next/+=token');
    expect(second.searchParams.get('document_revision_id')).toBe('-1');
  });

  it('stops a multi-page continuation cycle', async () => {
    let count = 0;
    const { client, fetcher } = fixture(() =>
      json({
        code: 0,
        data: { items: [], has_more: true, page_token: ++count % 2 ? 'first' : 'second' },
      }),
    );
    await expect(client.getBlocks('document_1')).rejects.toMatchObject({
      kind: 'invalid_response',
    });
    expect(fetcher).toHaveBeenCalledTimes(4);
  });

  it('enforces page and block limits', async () => {
    const pages = fixture(
      () => json({ code: 0, data: { items: [], has_more: true, page_token: 'next' } }),
      { maxPages: 1 },
    );
    await expect(pages.client.getBlocks('document_1')).rejects.toMatchObject({
      kind: 'limit_exceeded',
    });
    const blocks = fixture(
      () =>
        json({
          code: 0,
          data: {
            items: [
              { block_id: 'one', block_type: 1 },
              { block_id: 'two', block_type: 2 },
            ],
            has_more: false,
          },
        }),
      { maxBlocks: 1 },
    );
    await expect(blocks.client.getBlocks('document_1')).rejects.toMatchObject({
      kind: 'limit_exceeded',
    });
  });

  it('rejects a mismatched document ID or invalid revision', async () => {
    const { client } = fixture(() =>
      json({
        code: 0,
        data: {
          document: {
            document_id: 'another',
            title: 'Synthetic',
            revision_id: 1,
          },
        },
      }),
    );
    await expect(client.getDocument('document_1')).rejects.toMatchObject({
      kind: 'invalid_response',
    });
  });

  it.each([
    [403, 131006, 'source_denied', 'list'],
    [403, 1770032, 'document_denied', 'document'],
    [404, 1770002, 'not_found', 'document'],
    [400, 1770003, 'not_found', 'document'],
    [401, 99991663, 'auth', 'document'],
    [400, 99991672, 'auth', 'document'],
  ])(
    'classifies HTTP %s code %s as %s without upstream details',
    async (status, code, kind, method) => {
      const { client, sleep } = fixture(() =>
        json({ code, msg: 'synthetic_secret source_token body' }, status as number),
      );
      const pending =
        method === 'list' ? client.listNodes('space_1') : client.getDocument('document_1');
      const error = await pending.catch((error: unknown) => error);
      expect(error).toBeInstanceOf(FeishuError);
      expect(error).toMatchObject({ kind });
      expect(String(error)).not.toMatch(/synthetic_secret|source_token/);
      expect(JSON.stringify(error)).not.toMatch(/synthetic_secret|source_token/);
      expect(sleep).not.toHaveBeenCalled();
    },
  );

  it('retries 429 and a network failure while respecting Retry-After', async () => {
    let count = 0;
    const { client, sleep } = fixture(() => {
      count++;
      if (count === 1) {
        return json({ code: 99991400 }, 429, { 'retry-after': '2' });
      }
      if (count === 2) {
        throw new Error('synthetic_secret');
      }
      return json({ code: 0, data: { node: syntheticNode } });
    });
    await expect(client.getNode('leaf_1')).resolves.toEqual(syntheticNode);
    expect(sleep.mock.calls).toEqual([[2000], [1000]]);
  });

  it('respects an HTTP-date Retry-After and defers delays beyond its retry window', async () => {
    const now = Date.UTC(2026, 0, 1);
    const { client, sleep } = fixture(
      () =>
        json({}, 503, {
          'retry-after': new Date(now + 120_000).toUTCString(),
        }),
      { now: () => now },
    );
    await expect(client.getNode('leaf_1')).rejects.toMatchObject({
      kind: 'transient',
      retryAfterMs: 120_000,
    });
    expect(sleep).not.toHaveBeenCalled();
  });

  it('aborts timed-out requests and never exposes fetch errors', async () => {
    const { client } = fixture(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init.signal?.addEventListener('abort', () => reject(new Error('synthetic_secret')));
        }),
      { timeoutMs: 5, maxRetries: 0 },
    );
    await expect(client.getNode('leaf_1')).rejects.toMatchObject({
      kind: 'transient',
      message: 'FEISHU_TRANSIENT',
    });
  });

  it('stops failed credentials without retrying or leaking the returned message', async () => {
    const fetcher = jest.fn(async () => json({ code: 10003, msg: 'synthetic_secret' }));
    const client = new FeishuClient({
      appId: 'synthetic_app',
      appSecret: 'synthetic_secret',
      fetch: fetcher,
    });
    await expect(client.getNode('leaf_1')).rejects.toMatchObject({ kind: 'auth', scope: 'source' });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('returns partial metadata failures and sends object tokens with their types', async () => {
    const { client, fetcher } = fixture(() =>
      json({
        code: 0,
        data: {
          metas: [{ doc_token: 'one', doc_type: 'docx', title: 'One', latest_modify_time: '123' }],
          failed_list: [{ token: 'two', code: 970003 }],
        },
      }),
    );
    const docs = [
      { doc_token: 'one', doc_type: 'docx' },
      { doc_token: 'two', doc_type: 'docx' },
    ];
    expect((await client.getMetadata(docs)).failed_list).toEqual([{ token: 'two', code: 970003 }]);
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({
      request_docs: docs,
      with_url: false,
    });
  });

  it('rejects omitted metadata outcomes and oversized batches', async () => {
    const { client } = fixture(() => json({ code: 0, data: { metas: [], failed_list: [] } }));
    await expect(
      client.getMetadata([{ doc_token: 'one', doc_type: 'docx' }]),
    ).rejects.toMatchObject({ kind: 'invalid_response' });
    await expect(
      client.getMetadata(
        Array.from({ length: 201 }, () => ({ doc_token: 'one', doc_type: 'docx' })),
      ),
    ).rejects.toMatchObject({ kind: 'limit_exceeded' });
  });

  it('accepts omitted failure lists only when all metadata requests are accounted for', async () => {
    const { client } = fixture(() =>
      json({ code: 0, data: { metas: [{ doc_token: 'one', doc_type: 'docx', title: 'One' }] } }),
    );
    expect(
      (await client.getMetadata([{ doc_token: 'one', doc_type: 'docx' }])).failed_list,
    ).toEqual([]);
  });

  it('downloads bounded media without following arbitrary redirects', async () => {
    const redirected = fixture(
      () => new Response(null, { status: 302, headers: { location: 'http://127.0.0.1/private' } }),
    );
    await expect(redirected.client.downloadMedia('media_1')).rejects.toMatchObject({
      kind: 'invalid_response',
    });
    expect(redirected.fetcher).toHaveBeenCalledTimes(2);
    const bounded = fixture(
      () => new Response(new Uint8Array([1, 2, 3]), { headers: { 'content-type': 'image/png' } }),
      { maxMediaBytes: 3 },
    );
    expect(await bounded.client.downloadMedia('media_1')).toEqual({
      buffer: Buffer.from([1, 2, 3]),
      contentType: 'image/png',
    });
  });

  it.each([false, true])(
    'rejects an oversized media body with content-length=%s',
    async (declared) => {
      const { client } = fixture(
        () =>
          new Response(new Uint8Array([1, 2, 3, 4]), {
            headers: declared ? { 'content-length': '4' } : {},
          }),
        { maxMediaBytes: 3 },
      );
      await expect(client.downloadMedia('media_1')).rejects.toMatchObject({
        kind: 'limit_exceeded',
      });
    },
  );

  it('rejects identifiers that could change API path or query structure', async () => {
    const { client, fetcher } = fixture(() => json({}));
    await expect(client.downloadMedia('../auth?token=secret')).rejects.toMatchObject({
      kind: 'invalid_response',
    });
    expect(fetcher).not.toHaveBeenCalled();
  });
});
