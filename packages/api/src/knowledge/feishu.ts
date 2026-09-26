const origin = 'https://open.feishu.cn';

export type FeishuErrorKind =
  | 'auth'
  | 'source_denied'
  | 'document_denied'
  | 'not_found'
  | 'transient'
  | 'invalid_response'
  | 'limit_exceeded';

type Scope = 'source' | 'document';
type Fetcher = (input: string, init?: RequestInit) => Promise<Response>;

export class FeishuError extends Error {
  readonly code: string;

  constructor(
    readonly kind: FeishuErrorKind,
    readonly scope: Scope = 'document',
    readonly retryAfterMs?: number,
  ) {
    const code = `FEISHU_${kind.toUpperCase()}`;
    super(code);
    this.name = 'FeishuError';
    this.code = code;
  }
}

export type FeishuNode = {
  space_id: string;
  node_token: string;
  obj_token: string;
  obj_type: string;
  parent_node_token?: string;
  has_child: boolean;
  node_type?: string;
  origin_space_id?: string;
  origin_node_token?: string;
  title: string;
  obj_edit_time?: string;
  obj_create_time?: string;
  node_create_time?: string;
};

export type FeishuBlock = Record<string, unknown> & {
  block_id: string;
  block_type: number;
  parent_id?: string;
  children?: string[];
};

export type FeishuDocument = {
  document_id: string;
  title: string;
  revision_id: number;
};

export type FeishuMetadata = {
  doc_token: string;
  doc_type: string;
  title: string;
  create_time?: string;
  latest_modify_time?: string;
};

export type FeishuClientOptions = {
  appId: string;
  appSecret: string;
  timeoutMs?: number;
  maxRetries?: number;
  maxMediaBytes?: number;
  maxBlocks?: number;
  maxPages?: number;
  fetch?: Fetcher;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
  random?: () => number;
};

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new FeishuError('invalid_response');
  }
  return value as Record<string, unknown>;
}

function identifier(value: unknown, scope: Scope = 'document'): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{1,256}$/.test(value)) {
    throw new FeishuError('invalid_response', scope);
  }
  return value;
}

function finiteOption(value: number | undefined, fallback: number, max: number, min = 1): number {
  if (value == null) {
    return fallback;
  }
  if (!Number.isSafeInteger(value) || value < min || value > max) {
    throw new FeishuError('invalid_response', 'source');
  }
  return value;
}

function node(value: unknown, scope: Scope): FeishuNode {
  const item = record(value);
  identifier(item.space_id, scope);
  identifier(item.node_token, scope);
  identifier(item.obj_token, scope);
  if (
    typeof item.obj_type !== 'string' ||
    typeof item.title !== 'string' ||
    typeof item.has_child !== 'boolean'
  ) {
    throw new FeishuError('invalid_response', scope);
  }
  return item as FeishuNode;
}

function page(data: Record<string, unknown>, previous?: string) {
  if (typeof data.has_more !== 'boolean' || !Array.isArray(data.items)) {
    throw new FeishuError('invalid_response');
  }
  if (
    data.has_more &&
    (typeof data.page_token !== 'string' ||
      !data.page_token ||
      data.page_token.length > 8192 ||
      data.page_token === previous)
  ) {
    throw new FeishuError('invalid_response');
  }
  return {
    items: data.items,
    hasMore: data.has_more,
    pageToken: data.has_more ? (data.page_token as string) : undefined,
  };
}

function classify(status: number, code: number, scope: Scope, tokenRequest: boolean) {
  if (status === 429 || status >= 500 || code === 99991400 || code === 99991403) {
    return 'transient';
  }
  if (
    status === 401 ||
    tokenRequest ||
    (code >= 99991600 && code <= 99991699) ||
    code === 10003 ||
    code === 10014
  ) {
    return 'auth';
  }
  if ([131005, 1770002, 1770003].includes(code) || status === 404) {
    return 'not_found';
  }
  if (status === 403 || [131006, 1770032].includes(code)) {
    return scope === 'source' ? 'source_denied' : 'document_denied';
  }
  return 'invalid_response';
}

export class FeishuClient {
  private readonly fetcher: Fetcher;
  private readonly sleep: (ms: number) => Promise<void>;
  private readonly now: () => number;
  private readonly random: () => number;
  private readonly timeoutMs: number;
  private readonly maxRetries: number;
  private readonly maxMediaBytes: number;
  private readonly maxBlocks: number;
  private readonly maxPages: number;
  private accessToken?: { value: string; expiresAt: number };
  private tokenFlight?: Promise<string>;

  constructor(private readonly options: FeishuClientOptions) {
    if (!options.appId?.trim() || !options.appSecret?.trim()) {
      throw new FeishuError('auth', 'source');
    }
    this.fetcher = options.fetch ?? globalThis.fetch;
    this.sleep = options.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
    this.now = options.now ?? Date.now;
    this.random = options.random ?? Math.random;
    this.timeoutMs = finiteOption(options.timeoutMs, 30_000, 120_000);
    this.maxRetries = finiteOption(options.maxRetries, 3, 8, 0);
    this.maxMediaBytes = finiteOption(options.maxMediaBytes, 25 * 1024 * 1024, 1024 * 1024 * 1024);
    this.maxBlocks = finiteOption(options.maxBlocks, 50_000, 1_000_000);
    this.maxPages = finiteOption(options.maxPages, 10_000, 100_000);
  }

  private async token(): Promise<string> {
    if (this.accessToken && this.accessToken.expiresAt > this.now()) {
      return this.accessToken.value;
    }
    if (this.tokenFlight) {
      return this.tokenFlight;
    }
    this.tokenFlight = (async () => {
      const data = record(
        await this.request(
          '/open-apis/auth/v3/tenant_access_token/internal',
          {
            method: 'POST',
            body: JSON.stringify({
              app_id: this.options.appId,
              app_secret: this.options.appSecret,
            }),
          },
          'source',
          true,
        ),
      );
      if (
        typeof data.tenant_access_token !== 'string' ||
        !data.tenant_access_token ||
        typeof data.expire !== 'number' ||
        !Number.isFinite(data.expire) ||
        data.expire <= 0
      ) {
        throw new FeishuError('invalid_response', 'source');
      }
      this.accessToken = {
        value: data.tenant_access_token,
        expiresAt: this.now() + data.expire * 1000 - Math.min(60_000, data.expire * 100),
      };
      return this.accessToken.value;
    })();
    try {
      return await this.tokenFlight;
    } finally {
      this.tokenFlight = undefined;
    }
  }

  private retryDelay(response: Response, attempt: number): number {
    const raw = response.headers.get('retry-after');
    const seconds = raw == null ? NaN : Number(raw);
    const date = raw == null ? NaN : Date.parse(raw);
    let serverDelay = 0;
    if (Number.isFinite(seconds)) {
      serverDelay = Math.max(0, seconds * 1000);
    } else if (Number.isFinite(date)) {
      serverDelay = Math.max(0, date - this.now());
    }
    return Math.max(serverDelay, this.backoff(attempt));
  }

  private backoff(attempt: number): number {
    return Math.min(30_000, 500 * 2 ** attempt) + Math.floor(this.random() * 250);
  }

  private async read(response: Response, limit: number, scope: Scope): Promise<Buffer> {
    const declared = Number(response.headers.get('content-length'));
    if (Number.isFinite(declared) && declared > limit) {
      await response.body?.cancel();
      throw new FeishuError('limit_exceeded', scope);
    }
    if (!response.body) {
      return Buffer.alloc(0);
    }
    const reader = response.body.getReader();
    let size = 0;
    const chunks: Uint8Array[] = [];
    try {
      for (;;) {
        const chunk = await reader.read();
        if (chunk.done) {
          break;
        }
        size += chunk.value.byteLength;
        if (size > limit) {
          await reader.cancel();
          throw new FeishuError('limit_exceeded', scope);
        }
        chunks.push(chunk.value);
      }
      return Buffer.concat(chunks, size);
    } finally {
      reader.releaseLock();
    }
  }

  private async request(
    path: string,
    init: RequestInit,
    scope: Scope,
    tokenRequest = false,
    binary = false,
  ): Promise<unknown> {
    const url = new URL(path, origin);
    if (url.origin !== origin || !url.pathname.startsWith('/open-apis/')) {
      throw new FeishuError('invalid_response', scope);
    }
    const accessToken = tokenRequest ? undefined : await this.token();
    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
      let delay = this.backoff(attempt);
      try {
        const response = await this.fetcher(url.href, {
          ...init,
          redirect: 'manual',
          signal: controller.signal,
          headers: {
            'Content-Type': 'application/json; charset=utf-8',
            ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
          },
        });
        if (response.status >= 300 && response.status < 400) {
          await response.body?.cancel();
          throw new FeishuError('invalid_response', scope);
        }
        delay = this.retryDelay(response, attempt);
        if (response.status === 429 || response.status >= 500) {
          await response.body?.cancel();
          throw new FeishuError('transient', scope, delay);
        }
        const bytes = await this.read(
          response,
          binary ? this.maxMediaBytes : 32 * 1024 * 1024,
          scope,
        );
        const contentType = (response.headers.get('content-type') ?? 'application/octet-stream')
          .split(';')[0]
          .trim()
          .toLowerCase();
        if (binary && response.ok && contentType !== 'application/json') {
          return { buffer: bytes, contentType };
        }
        let parsed: Record<string, unknown>;
        try {
          parsed = record(JSON.parse(bytes.toString('utf8')));
        } catch {
          throw new FeishuError(
            response.ok ? 'invalid_response' : classify(response.status, -1, scope, tokenRequest),
            scope,
          );
        }
        if (!response.ok || parsed.code !== 0) {
          const kind = classify(
            response.status,
            typeof parsed.code === 'number' ? parsed.code : -1,
            scope,
            tokenRequest,
          );
          if (kind === 'auth') {
            this.accessToken = undefined;
          }
          throw new FeishuError(kind, scope, kind === 'transient' ? delay : undefined);
        }
        if (binary) {
          throw new FeishuError('invalid_response', scope);
        }
        return tokenRequest ? parsed : record(parsed.data);
      } catch (error) {
        const safe =
          error instanceof FeishuError ? error : new FeishuError('transient', scope, delay);
        if (safe.kind !== 'transient' || attempt === this.maxRetries || delay > 60_000) {
          throw safe;
        }
      } finally {
        clearTimeout(timeout);
      }
      await this.sleep(delay);
    }
    throw new FeishuError('transient', scope);
  }

  async resolveSpace(
    wikiUrl: string,
    expectedSpaceId?: string,
  ): Promise<{ spaceId: string; node: FeishuNode }> {
    let url: URL;
    try {
      url = new URL(wikiUrl);
    } catch {
      throw new FeishuError('invalid_response', 'source');
    }
    const match = url.pathname.match(/^\/wiki\/([A-Za-z0-9_-]{1,256})\/?$/);
    if (
      url.protocol !== 'https:' ||
      !url.hostname.endsWith('.feishu.cn') ||
      url.username ||
      url.password ||
      url.port ||
      !match
    ) {
      throw new FeishuError('invalid_response', 'source');
    }
    const result = await this.readNode(match[1], 'source');
    if (expectedSpaceId && result.space_id !== expectedSpaceId) {
      throw new FeishuError('source_denied', 'source');
    }
    return { spaceId: result.space_id, node: result };
  }

  private async readNode(nodeToken: string, scope: Scope) {
    const query = new URLSearchParams({ token: identifier(nodeToken, scope) });
    const data = record(
      await this.request(`/open-apis/wiki/v2/spaces/get_node?${query}`, {}, scope),
    );
    return node(data.node, scope);
  }

  getNode(nodeToken: string): Promise<FeishuNode> {
    return this.readNode(nodeToken, 'document');
  }

  async listNodes(
    spaceId: string,
    parentNodeToken?: string,
    pageToken?: string,
  ): Promise<{ items: FeishuNode[]; hasMore: boolean; pageToken?: string }> {
    const query = new URLSearchParams({ page_size: '50' });
    if (parentNodeToken) {
      query.set('parent_node_token', identifier(parentNodeToken, 'source'));
    }
    if (pageToken) {
      query.set('page_token', pageToken);
    }
    const data = record(
      await this.request(
        `/open-apis/wiki/v2/spaces/${identifier(spaceId, 'source')}/nodes?${query}`,
        {},
        'source',
      ),
    );
    const result = page(data, pageToken);
    return { ...result, items: result.items.map((item) => node(item, 'source')) };
  }

  async getDocument(objToken: string): Promise<FeishuDocument> {
    const data = record(
      await this.request(`/open-apis/docx/v1/documents/${identifier(objToken)}`, {}, 'document'),
    );
    const document = record(data.document);
    if (
      document.document_id !== objToken ||
      typeof document.title !== 'string' ||
      typeof document.revision_id !== 'number' ||
      !Number.isSafeInteger(document.revision_id) ||
      document.revision_id < 1
    ) {
      throw new FeishuError('invalid_response');
    }
    return document as FeishuDocument;
  }

  async getBlocks(objToken: string): Promise<FeishuBlock[]> {
    const blocks: FeishuBlock[] = [];
    const tokens = new Set<string>();
    let next: string | undefined;
    for (let count = 0; count < this.maxPages; count++) {
      const query = new URLSearchParams({
        page_size: '500',
        document_revision_id: '-1',
        user_id_type: 'open_id',
      });
      if (next) {
        query.set('page_token', next);
      }
      const data = record(
        await this.request(
          `/open-apis/docx/v1/documents/${identifier(objToken)}/blocks?${query}`,
          {},
          'document',
        ),
      );
      const result = page(data, next);
      for (const value of result.items) {
        const block = record(value);
        identifier(block.block_id);
        if (typeof block.block_type !== 'number' || !Number.isSafeInteger(block.block_type)) {
          throw new FeishuError('invalid_response');
        }
        blocks.push(block as FeishuBlock);
        if (blocks.length > this.maxBlocks) {
          throw new FeishuError('limit_exceeded');
        }
      }
      if (!result.hasMore) {
        return blocks;
      }
      next = result.pageToken;
      if (!next || tokens.has(next)) {
        throw new FeishuError('invalid_response');
      }
      tokens.add(next);
    }
    throw new FeishuError('limit_exceeded');
  }

  async getMetadata(docs: { doc_token: string; doc_type: string }[]): Promise<{
    metas: FeishuMetadata[];
    failed_list: { token: string; code: number }[];
  }> {
    if (docs.length < 1 || docs.length > 200) {
      throw new FeishuError('limit_exceeded');
    }
    const data = record(
      await this.request(
        '/open-apis/drive/v1/metas/batch_query?user_id_type=open_id',
        {
          method: 'POST',
          body: JSON.stringify({
            request_docs: docs.map((doc) => ({
              doc_token: identifier(doc.doc_token),
              doc_type: identifier(doc.doc_type),
            })),
            with_url: false,
          }),
        },
        'document',
      ),
    );
    if (
      !Array.isArray(data.metas) ||
      (data.failed_list != null && !Array.isArray(data.failed_list))
    ) {
      throw new FeishuError('invalid_response');
    }
    const result = {
      metas: data.metas.map((value) => {
        const meta = record(value);
        identifier(meta.doc_token);
        if (typeof meta.doc_type !== 'string' || typeof meta.title !== 'string') {
          throw new FeishuError('invalid_response');
        }
        return meta as FeishuMetadata;
      }),
      failed_list: ((data.failed_list ?? []) as unknown[]).map((value) => {
        const failed = record(value);
        identifier(failed.token);
        if (typeof failed.code !== 'number') {
          throw new FeishuError('invalid_response');
        }
        return failed as { token: string; code: number };
      }),
    };
    const requested = new Set(docs.map((doc) => doc.doc_token));
    const returned = [
      ...result.metas.map((meta) => meta.doc_token),
      ...result.failed_list.map((item) => item.token),
    ];
    if (
      returned.length !== requested.size ||
      new Set(returned).size !== returned.length ||
      returned.some((token) => !requested.has(token))
    ) {
      throw new FeishuError('invalid_response');
    }
    return result;
  }

  async downloadMedia(token: string): Promise<{ buffer: Buffer; contentType: string }> {
    return (await this.request(
      `/open-apis/drive/v1/medias/${identifier(token)}/download`,
      {},
      'document',
      false,
      true,
    )) as { buffer: Buffer; contentType: string };
  }
}

export type FeishuReader = Pick<
  FeishuClient,
  | 'resolveSpace'
  | 'getNode'
  | 'listNodes'
  | 'getDocument'
  | 'getBlocks'
  | 'getMetadata'
  | 'downloadMedia'
>;
