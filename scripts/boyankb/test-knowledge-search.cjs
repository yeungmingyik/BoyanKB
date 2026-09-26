const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const fixture = require('./knowledge-qa-fixtures.cjs');

const fixtureAgent = 'agent_sync_acceptance';
const fixtureSpace = 'space_sync_acceptance';
const fixtureUrl = 'https://fixture.feishu.cn/wiki/fixture_leaf';
const reportPath = '/app/data/knowledge-search-integration-results.json';
const accountsPath = '/app/data/browser-smoke-accounts.json';
const fixturePath = '/app/data/qa-fixture.json';
const report = { passed: false, checks: [], counts: {}, calibration: [] };
const nonce = randomUUID().replaceAll('-', '');
let mongoose;
let server;
let restoreView;
let releaseBarrier;

function ensure(condition, code) {
  if (!condition) throw new Error(`KNOWLEDGE_SEARCH_TEST_${code}`);
}

function pass(name) {
  report.checks.push(name);
  process.stdout.write(`PASS ${name}\n`);
}

function safeCode(error) {
  const code = error?.code ?? error?.message;
  return typeof code === 'string' && /^KNOWLEDGE_[A-Z0-9_]+$/.test(code)
    ? code
    : 'KNOWLEDGE_SEARCH_TEST_FAILED';
}

async function request(
  route,
  { method = 'GET', session, body, base = 'http://127.0.0.1:3080', headers = {} } = {},
) {
  const response = await fetch(`${base}${route}`, {
    method,
    redirect: 'manual',
    headers: {
      Origin: process.env.DOMAIN_CLIENT,
      'Content-Type': 'application/json',
      'User-Agent':
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36',
      ...(session ? { Authorization: `Bearer ${session.token}` } : {}),
      ...headers,
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal: AbortSignal.timeout(60000),
  });
  const text = await response.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = undefined;
  }
  return { status: response.status, data, headers: response.headers };
}

function status(response, expected, code) {
  ensure(response.status === expected, `${code}_HTTP_${response.status}`);
  return response;
}

function readingBlocks(blocks) {
  return blocks.flatMap((block) => [block, ...readingBlocks(block.children ?? [])]);
}

async function main() {
  ensure(process.env.NODE_ENV === 'test', 'ENVIRONMENT');
  ensure(process.env.BOYANKB_TEST_INSTANCE === 'boyankb-librechat-sync-test', 'INSTANCE');
  ensure(process.env.BOYANKB_KNOWLEDGE_AGENT_ID === fixtureAgent, 'AGENT');
  ensure(process.env.RAG_API_URL === 'http://rag:8000', 'RAG_ORIGIN');
  ensure(
    process.env.FEISHU_APP_ID === 'fixture_app' &&
      process.env.FEISHU_APP_SECRET === 'fixture_secret',
    'SYNTHETIC_CREDENTIALS',
  );
  ensure(
    !process.env.EMAIL_HOST && !process.env.EMAIL_SERVICE && !process.env.MAILGUN_API_KEY,
    'EMAIL_DISABLED',
  );
  ensure(
    Array.isArray(fixture.documents) &&
      fixture.documents.length >= 4 &&
      fixture.documents.length <= 30,
    'FIXTURE_DOCUMENTS',
  );
  ensure(
    fixture.documents.every(
      (item) =>
        /^[a-z0-9_-]{1,64}$/.test(item.id) &&
        /^[a-z0-9_-]{1,48}$/.test(item.category) &&
        typeof item.text === 'string' &&
        item.text.trim() &&
        typeof item.title === 'string' &&
        item.title.trim(),
    ),
    'FIXTURE_STRUCTURE',
  );
  ensure(
    new Set(fixture.documents.map((item) => item.id)).size === fixture.documents.length,
    'FIXTURE_UNIQUENESS',
  );
  require('module-alias')({ base: path.resolve(process.cwd(), 'api') });
  mongoose = require('mongoose');
  const { createModels, runAsSystem, logger } = require('@librechat/data-schemas');
  logger.silent = true;
  const { connectDb } = require('~/db');
  const db = require('~/models');
  const { getAppConfig } = require('~/server/services/Config');
  const { AccessRoleIds, knowledgeSearchConfigSchema } = require('librechat-data-provider');
  const {
    createKnowledgeService,
    createNativeKnowledgeIndexer,
    createNativeKnowledgeSearch,
    createKnowledgeSearchService,
    createKnowledgeRouter,
    LocalKnowledgeBlobStore,
    FeishuError,
    resolveKnowledgeConfig,
  } = require('@librechat/api');
  await connectDb();
  const models = createModels(mongoose);
  ensure(
    (await models.User.countDocuments({ email: { $not: /@boyankb-acceptance\.invalid$/ } })) === 0,
    'SYNTHETIC_DATABASE',
  );
  ensure(
    (await models.Agent.countDocuments({ id: { $ne: fixtureAgent } })) === 0,
    'ISOLATED_AGENT_DATABASE',
  );
  ensure(
    (await models.KnowledgeSource.countDocuments({ agentId: { $ne: fixtureAgent } })) === 0,
    'ISOLATED_SOURCE_DATABASE',
  );
  const hello = await mongoose.connection.db.admin().command({ hello: 1 });
  ensure(Boolean(hello.setName && hello.isWritablePrimary), 'REPLICA_PRIMARY');
  const knowledge = resolveKnowledgeConfig(
    (await getAppConfig({ baseOnly: true })).config?.knowledge,
  );
  ensure(
    knowledge?.enabled &&
      knowledge.sync?.enabled &&
      knowledge.agentId === fixtureAgent &&
      knowledge.sync.wikiUrl === fixtureUrl &&
      knowledge.sync.spaceId === fixtureSpace,
    'CONFIG_SCOPE',
  );
  const searchConfig = knowledgeSearchConfigSchema.parse(knowledge.search ?? {});
  ensure(fs.existsSync(accountsPath), 'SYNC_FIXTURE_REQUIRED');
  const accounts = JSON.parse(fs.readFileSync(accountsPath, 'utf8'));
  const sessions = {};
  for (const name of ['admin', 'authorized', 'denied']) {
    ensure(accounts[name]?.email === `sync-${name}@boyankb-acceptance.invalid`, 'ACCOUNT_IDENTITY');
    const response = status(
      await request('/api/auth/login', { method: 'POST', body: accounts[name] }),
      200,
      'LOGIN',
    );
    ensure(typeof response.data?.token === 'string', 'LOGIN_TOKEN');
    sessions[name] = { token: response.data.token, user: response.data.user };
  }
  ensure(
    sessions.admin.user.role === 'ADMIN' && sessions.authorized.user.role === 'USER',
    'NATIVE_ROLES',
  );
  const agent = await db.getAgent({ id: fixtureAgent });
  ensure(agent?.author?.toString() === sessions.admin.user.id, 'NATIVE_OWNER');
  const permissionPath = `/api/permissions/agent/${agent._id.toString()}`;
  const grant = () =>
    request(permissionPath, {
      method: 'PUT',
      session: sessions.admin,
      body: {
        updated: [
          {
            type: 'user',
            id: sessions.authorized.user.id,
            accessRoleId: AccessRoleIds.AGENT_VIEWER,
          },
        ],
        removed: [{ type: 'user', id: sessions.denied.user.id }],
        public: false,
      },
    });
  const revoke = () =>
    request(permissionPath, {
      method: 'PUT',
      session: sessions.admin,
      body: {
        updated: [],
        removed: [{ type: 'user', id: sessions.authorized.user.id }],
        public: false,
      },
    });
  restoreView = async () => {
    status(await grant(), 200, 'RESTORE_VIEW');
  };
  await restoreView();
  status(
    await request('/api/knowledge/search', { method: 'POST', body: { query: '课程' } }),
    401,
    'ANONYMOUS_SEARCH',
  );
  status(
    await request('/api/knowledge/search', {
      method: 'POST',
      session: sessions.denied,
      body: { query: '课程' },
    }),
    403,
    'DENIED_SEARCH',
  );
  for (const field of ['file_ids', 'ownerId', 'agentId', 'entity_id']) {
    status(
      await request('/api/knowledge/search', {
        method: 'POST',
        session: sessions.authorized,
        body: { query: '课程', [field]: 'untrusted' },
      }),
      400,
      'SCOPE_INJECTION',
    );
  }
  pass('native authorization and search scope input boundaries');

  const categories = [...new Set(fixture.documents.map((item) => item.category))].sort();
  const categoryDocs = categories.map((category) => ({
    id: `directory_${category}`,
    title: `课程目录 ${category}`,
    category,
    text: `本目录包含：${fixture.documents
      .filter((item) => item.category === category)
      .map((item) => item.title)
      .join('；')}。`,
    directory: true,
  }));
  const documents = [...categoryDocs, ...fixture.documents];
  const byToken = new Map(documents.map((item) => [`qa_object_${item.id}`, item]));
  const state = { version: 1, updatedId: undefined };
  const sourceVersion = (item) => (item.id === fixture.documents[0].id ? state.version : 1);
  const node = (item) => ({
    space_id: fixtureSpace,
    node_token: `qa_node_${item.id}`,
    obj_token: `qa_object_${item.id}`,
    obj_type: 'docx',
    title: item.title,
    has_child: Boolean(item.directory),
    ...(item.directory ? {} : { parent_node_token: `qa_node_directory_${item.category}` }),
    obj_edit_time: String(1750000000 + sourceVersion(item)),
  });
  const sourceText = (item) =>
    item.text + (state.updatedId === item.id ? '\n同步版本校验标记QAXYZ二。' : '');
  const client = {
    async resolveSpace(url, expected) {
      ensure(url === fixtureUrl && expected === fixtureSpace, 'FAKE_RESOLVE_SCOPE');
      return { spaceId: fixtureSpace, node: node(fixture.documents[0]) };
    },
    async listNodes(spaceId, parent, cursor) {
      ensure(spaceId === fixtureSpace && !cursor, 'FAKE_LIST_SCOPE');
      if (!parent) return { items: categoryDocs.map(node), hasMore: false };
      const category = categoryDocs.find((item) => `qa_node_${item.id}` === parent);
      ensure(category, 'FAKE_PARENT_SCOPE');
      return {
        items: fixture.documents.filter((item) => item.category === category.category).map(node),
        hasMore: false,
      };
    },
    async getNode(token) {
      const item = documents.find((item) => `qa_node_${item.id}` === token);
      if (!item) throw new FeishuError('not_found');
      return node(item);
    },
    async getDocument(token) {
      const item = byToken.get(token);
      ensure(item, 'FAKE_DOCUMENT_SCOPE');
      return {
        document_id: token,
        title: item.title,
        revision_id: sourceVersion(item),
      };
    },
    async getBlocks(token) {
      const item = byToken.get(token);
      ensure(item, 'FAKE_BLOCK_SCOPE');
      const paragraphs = sourceText(item)
        .split('\n')
        .filter((text) => text.trim());
      return [
        {
          block_id: 'page',
          block_type: 1,
          page: { elements: [{ text_run: { content: item.title } }] },
          children: paragraphs.map((_, index) => `paragraph_${index}`),
        },
        ...paragraphs.map((text, index) => ({
          block_id: `paragraph_${index}`,
          parent_id: 'page',
          block_type: 2,
          text: { elements: [{ text_run: { content: text } }] },
        })),
      ];
    },
    async getMetadata() {
      return { metas: [], failed_list: [] };
    },
    async downloadMedia() {
      throw new Error('KNOWLEDGE_SEARCH_TEST_UNEXPECTED_MEDIA');
    },
  };
  const ownerId = sessions.admin.user.id;
  const indexer = createNativeKnowledgeIndexer({
    agentId: fixtureAgent,
    ownerId,
    baseUrl: 'http://rag:8000',
    version: process.env.BOYANKB_INDEX_VERSION || 'bge-small-zh-v1.5-7999e1d-v1',
    findFile: (filename) =>
      models.File.findOne({ filename, user: ownerId, context: 'knowledge', embedded: true }).lean(),
    getFile: db.findFileById,
    saveFile: (file) => db.createFile(file, true),
    deleteFile: (fileId) =>
      models.File.deleteOne({ file_id: fileId, user: ownerId, context: 'knowledge' }),
    timeoutMs: 180000,
  });
  const service = createKnowledgeService({
    models,
    client,
    indexer,
    config: { agentId: fixtureAgent, sync: knowledge.sync },
    blobs: new LocalKnowledgeBlobStore(
      process.env.BOYANKB_KNOWLEDGE_STORAGE_DIR || path.resolve('data/knowledge'),
    ),
    workerId: `search-fixture-${nonce}`,
  });
  await runAsSystem(() => service.ensureSource());
  const source = await service.source();
  ensure(!source.leaseOwner || source.leaseUntil <= new Date(), 'NO_ACTIVE_WORKER');
  await models.KnowledgeRun.updateMany(
    { sourceId: service.sourceId, status: { $in: ['queued', 'running'] } },
    {
      $set: {
        status: 'failed',
        errorCode: 'KNOWLEDGE_SEARCH_TEST_RESTART',
        finishedAt: new Date(),
      },
    },
  );
  async function sync(label) {
    const run = status(
      await request('/api/knowledge/sync-runs', {
        method: 'POST',
        session: sessions.admin,
        headers: { 'Idempotency-Key': `search-${label}-${nonce}` },
        body: { mode: 'full' },
      }),
      202,
      'ENQUEUE',
    ).data;
    await runAsSystem(() => service.processNext());
    const completed = status(
      await request(`/api/knowledge/sync-runs/${run.id}`, { session: sessions.admin }),
      200,
      'SYNC_RUN',
    ).data;
    ensure(
      completed.status === 'completed' &&
        completed.counts.failed === 0 &&
        completed.counts.published + completed.counts.unchanged === documents.length,
      'SYNC_COMPLETE',
    );
    return completed;
  }
  await sync('initial');
  ensure((await service.activeFileIds()).length === documents.length, 'ACTIVE_FILES');
  const firstRevisionCount = await models.KnowledgeRevision.countDocuments({
    sourceId: service.sourceId,
  });
  await sync('repeat');
  ensure(
    (await models.KnowledgeRevision.countDocuments({ sourceId: service.sourceId })) ===
      firstRevisionCount,
    'SNAPSHOT_IDEMPOTENCY',
  );
  pass('synthetic category documents published through real MongoDB and native RAG');

  const records = await models.KnowledgeDocument.find({
    sourceId: service.sourceId,
    status: 'published',
    objToken: { $in: [...byToken.keys()] },
  }).lean();
  const nodes = await models.KnowledgeNode.find({
    sourceId: service.sourceId,
    state: 'active',
    documentId: { $in: records.map((item) => item.id) },
  }).lean();
  const recordFor = (item) => records.find((record) => record.objToken === `qa_object_${item.id}`);
  const nodeFor = (item) => nodes.find((entry) => entry.documentId === recordFor(item).id);
  async function search(body, session = sessions.authorized) {
    return status(
      await request('/api/knowledge/search', { method: 'POST', session, body }),
      200,
      'SEARCH',
    ).data;
  }
  const relatedQuery =
    fixture.cases?.find((item) => item.kind === 'answerable')?.question ||
    fixture.documents[0].title;
  for (const mode of ['keyword', 'semantic', 'hybrid']) {
    const result = await search({
      query: relatedQuery,
      mode,
      limit: Math.min(20, searchConfig.maxResults),
    });
    ensure(result.items.length > 0 && result.sourceStatus === 'ready', 'SEARCH_HITS');
    ensure(
      result.items.some((hit) =>
        fixture.documents.some((item) => recordFor(item)?.id === hit.documentId),
      ),
      'CONTENT_DOCUMENT_HIT',
    );
    ensure(
      !JSON.stringify(result).includes('qa_object_') &&
        !JSON.stringify(result).includes('qa_node_'),
      'SOURCE_TOKEN_REDACTION',
    );
    for (const hit of result.items) {
      const document = status(
        await request(`/api/knowledge/documents/${hit.documentId}/revisions/${hit.revisionId}`, {
          session: sessions.authorized,
        }),
        200,
        'CITATION',
      ).data;
      const block = readingBlocks(document.blocks).find((item) => item.id === hit.blockId);
      ensure(
        document.title === hit.title && block?.text?.includes(hit.snippet),
        'CITATION_BLOCK_TEXT',
      );
      ensure(
        hit.href ===
          `/knowledge/documents/${hit.documentId}/revisions/${hit.revisionId}#block-${hit.blockId}`,
        'CITATION_HREF',
      );
    }
  }
  pass('HTTP keyword, semantic and hybrid search return canonical versioned block citations');

  const selectedCategory = categoryDocs.find(
    (item) => item.category === fixture.documents[0].category,
  );
  const directoryId = nodeFor(selectedCategory).id;
  const allowed = new Set([
    recordFor(selectedCategory).id,
    ...fixture.documents
      .filter((item) => item.category === selectedCategory.category)
      .map((item) => recordFor(item).id),
  ]);
  for (const mode of ['keyword', 'semantic', 'hybrid']) {
    const result = await search({ query: relatedQuery, mode, directoryId });
    ensure(
      result.items.length > 0 && result.items.every((hit) => allowed.has(hit.documentId)),
      'SUBTREE_SCOPE',
    );
  }
  const page = await search({ query: '课程', mode: 'keyword', limit: 1 });
  ensure(page.items.length === 1 && typeof page.nextCursor === 'string', 'FIRST_PAGE');
  const next = await search({ query: '课程', mode: 'keyword', limit: 1, cursor: page.nextCursor });
  ensure(
    next.snapshotId === page.snapshotId &&
      next.items.length === 1 &&
      `${next.items[0].documentId}:${next.items[0].blockId}` !==
        `${page.items[0].documentId}:${page.items[0].blockId}`,
    'NEXT_PAGE',
  );
  pass('directory subtree filtering and snapshot-bound pagination');

  const semantic = createNativeKnowledgeSearch({
    agentId: fixtureAgent,
    ownerId,
    baseUrl: 'http://rag:8000',
    getFile: db.findFileById,
    timeoutMs: 60000,
  });
  const nativeSearch = createKnowledgeSearchService({
    store: service,
    semantic,
    ownerId,
    config: knowledge.search,
  });
  const fileIds = await service.activeFileIds();
  const revisions = await models.KnowledgeRevision.find({
    id: { $in: records.map((item) => item.activeRevisionId) },
    sourceId: service.sourceId,
    status: 'published',
  }).lean();
  const byFile = new Map(
    revisions.flatMap((revision) => revision.nativeFileIds.map((id) => [id, revision.text])),
  );
  for (const [kind, query] of [
    ['related', relatedQuery],
    ['unrelated-finance', '明天纽约期货原油价格是多少？'],
    ['unrelated-medical', '申请住院医疗保险需要多少钱？'],
  ]) {
    const results = await runAsSystem(() =>
      semantic.query({ query, fileIds, limit: searchConfig.maxSemanticHits }),
    );
    ensure(
      results.every(
        (hit) =>
          Number.isFinite(hit.score) &&
          hit.score >= 0 &&
          hit.score <= 1 &&
          byFile.get(hit.fileId)?.includes(hit.text.trim()),
      ),
      'REAL_RAG_SCOPE_TEXT',
    );
    report.calibration.push({
      kind,
      resultCount: results.length,
      maxScore: results.length ? Math.max(...results.map((hit) => hit.score)) : null,
      scores: results.map((hit) => Number(hit.score.toFixed(4))),
    });
  }
  pass('real query_multiple scope, snapshot text membership and score calibration');

  const before = await search({ query: relatedQuery, mode: 'hybrid' });
  state.updatedId = fixture.documents[0].id;
  state.version = 2;
  await sync('version');
  const changed = await search({ query: '同步版本校验标记QAXYZ二', mode: 'keyword' });
  const targetId = recordFor(fixture.documents[0]).id;
  ensure(
    changed.items.some(
      (hit) =>
        hit.documentId === targetId &&
        hit.revisionId !== recordFor(fixture.documents[0]).activeRevisionId,
    ),
    'ACTIVE_REVISION_SWITCH',
  );
  ensure(
    !(await runAsSystem(() => nativeSearch.validateHits(before.items, before.snapshotId))),
    'STALE_CONTEXT_REJECTED',
  );
  status(
    await request('/api/knowledge/search', {
      method: 'POST',
      session: sessions.authorized,
      body: { query: '课程', mode: 'keyword', limit: 1, cursor: page.nextCursor },
    }),
    409,
    'STALE_CURSOR',
  );
  state.updatedId = undefined;
  state.version = 3;
  await sync('restore-content');
  pass('published revision changes invalidate search cursors and previous answer contexts');

  const express = require('express');
  const passport = require('passport');
  const jwtLogin = require('~/strategies/jwtStrategy');
  const requireJwtAuth = require('~/server/middleware/requireJwtAuth');
  const checkBan = require('~/server/middleware/checkBan');
  const knowledgeAccess = require('~/server/middleware/knowledgeAccess');
  passport.use(jwtLogin());
  let notifyEntered;
  const entered = new Promise((resolve) => {
    notifyEntered = resolve;
  });
  const barrier = new Promise((resolve) => {
    releaseBarrier = resolve;
  });
  const delayed = createKnowledgeSearchService({
    store: service,
    ownerId,
    config: { ...knowledge.search, requestTimeoutMs: 60000 },
    semantic: {
      query: async (input) => {
        const result = await semantic.query(input);
        notifyEntered();
        await barrier;
        return result;
      },
    },
  });
  const app = express();
  app.use(express.json());
  app.use(passport.initialize());
  app.use(
    '/api/knowledge',
    createKnowledgeRouter({
      getService: async () => ({ search: delayed.search.bind(delayed) }),
      authorize: (req, res, nextMiddleware) =>
        requireJwtAuth(req, res, (authError) => {
          if (authError) return nextMiddleware(authError);
          checkBan(req, res, (banError) => {
            if (banError) return nextMiddleware(banError);
            return knowledgeAccess.authorize(req, res, nextMiddleware);
          });
        }),
    }),
  );
  server = await new Promise((resolve) => {
    const instance = app.listen(0, '127.0.0.1', () => resolve(instance));
  });
  const base = `http://127.0.0.1:${server.address().port}`;
  const pending = request('/api/knowledge/search', {
    method: 'POST',
    session: sessions.authorized,
    base,
    body: { query: relatedQuery, mode: 'hybrid' },
  });
  let timeout;
  try {
    await Promise.race([
      entered,
      pending.then(() => {
        throw new Error('KNOWLEDGE_SEARCH_TEST_BARRIER_NOT_REACHED');
      }),
      new Promise((_, reject) => {
        timeout = setTimeout(
          () => reject(new Error('KNOWLEDGE_SEARCH_TEST_BARRIER_TIMEOUT')),
          30000,
        );
      }),
    ]);
    status(await revoke(), 200, 'REVOKE_VIEW');
    releaseBarrier();
    const rejected = status(await pending, 403, 'IN_FLIGHT_REVOKED_SEARCH');
    ensure(!Array.isArray(rejected.data?.items), 'REVOKED_NO_HITS');
    status(
      await request('/api/knowledge/search', {
        method: 'POST',
        session: sessions.authorized,
        body: { query: relatedQuery },
      }),
      403,
      'REVOKED_OLD_TOKEN',
    );
    ensure(
      (await models.Session.countDocuments({ user: sessions.authorized.user.id })) === 0,
      'REVOKED_REFRESH_SESSIONS',
    );
  } finally {
    clearTimeout(timeout);
    releaseBarrier();
    await restoreView();
    await new Promise((resolve) => server.close(resolve));
    server = undefined;
  }
  ensure((await search({ query: relatedQuery })).items.length > 0, 'RESTORED_SEARCH');
  pass(
    'native VIEW removal stops an in-flight search and rejects old tokens without returning hits',
  );

  const publicDocuments = [];
  const publicDirectories = [];
  for (const item of documents) {
    const record = recordFor(item);
    const content = status(
      await request(`/api/knowledge/documents/${record.id}`, { session: sessions.authorized }),
      200,
      'FINAL_DOCUMENT',
    ).data;
    const entry = {
      fixtureId: item.id,
      documentId: record.id,
      nodeId: nodeFor(item).id,
      revisionId: content.revision.id,
      title: content.title,
      category: item.category,
      blockIds: readingBlocks(content.blocks)
        .filter((block) => block.text)
        .map((block) => block.id),
      sourceUpdatedAt: content.revision.sourceUpdatedAt,
    };
    (item.directory ? publicDirectories : publicDocuments).push(entry);
  }
  fs.writeFileSync(
    fixturePath,
    JSON.stringify(
      {
        fixtureVersion: 1,
        agentId: fixtureAgent,
        documents: publicDocuments,
        directories: publicDirectories,
        at: new Date().toISOString(),
      },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  report.counts = {
    documents: publicDocuments.length,
    directories: publicDirectories.length,
    activeFiles: (await service.activeFileIds()).length,
    cases: fixture.cases?.length ?? 0,
    scenarios: report.checks.length,
  };
  report.passed = true;
}

main()
  .catch((error) => {
    report.errorCode = safeCode(error);
    process.stderr.write(`${report.errorCode}\n`);
  })
  .finally(async () => {
    releaseBarrier?.();
    if (restoreView)
      await restoreView().catch(() => {
        report.passed = false;
        report.restoreFailed = true;
      });
    if (server) await new Promise((resolve) => server.close(resolve));
    report.at = new Date().toISOString();
    if (
      process.env.BOYANKB_TEST_INSTANCE === 'boyankb-librechat-sync-test' &&
      process.env.NODE_ENV === 'test'
    )
      fs.writeFileSync(reportPath, JSON.stringify(report, null, 2), { mode: 0o600 });
    await mongoose?.disconnect().catch(() => {});
    process.exit(report.passed ? 0 : 1);
  });
