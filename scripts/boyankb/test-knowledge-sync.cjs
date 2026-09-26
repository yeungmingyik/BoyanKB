const fs = require('node:fs');
const path = require('node:path');
const { randomBytes, randomUUID } = require('node:crypto');
const { spawn } = require('node:child_process');

const fixtureAgent = 'agent_sync_acceptance';
const fixtureSpace = 'space_sync_acceptance';
const fixtureUrl = 'https://fixture.feishu.cn/wiki/fixture_leaf';
const reportPath = '/app/data/knowledge-sync-integration-results.json';
const accountsPath = '/app/data/browser-smoke-accounts.json';
const browserPath = '/app/data/knowledge-browser-fixture.json';
const report = { passed: false, checks: [], at: '', counts: {} };
const nonce = randomUUID().replaceAll('-', '');
const imageBytes = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j3ioAAAAASUVORK5CYII=',
  'base64',
);
let mongoose;

function ensure(condition, code) {
  if (!condition) {
    throw new Error(`KNOWLEDGE_SYNC_TEST_${code}`);
  }
}

function pass(name) {
  report.checks.push(name);
  console.log(`PASS ${name}`);
}

function safeCode(error) {
  const code = error?.code ?? error?.message;
  return typeof code === 'string' && /^KNOWLEDGE_[A-Z0-9_]+$/.test(code)
    ? code
    : 'KNOWLEDGE_SYNC_TEST_FAILED';
}

async function request(route, { method = 'GET', session, body, headers = {} } = {}) {
  const response = await fetch(`http://127.0.0.1:3080${route}`, {
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
    signal: AbortSignal.timeout(30000),
  });
  const bytes = Buffer.from(await response.arrayBuffer());
  let data;
  try {
    data = JSON.parse(bytes.toString('utf8'));
  } catch {
    data = undefined;
  }
  return { status: response.status, data, bytes, headers: response.headers };
}

function status(response, expected, code) {
  ensure(response.status === expected, `${code}_HTTP_${response.status}`);
  return response;
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
  require('module-alias')({ base: path.resolve(process.cwd(), 'api') });
  mongoose = require('mongoose');
  const { connectDb } = require('~/db');
  const db = require('~/models');
  const { getAppConfig } = require('~/server/services/Config');
  const { registerUser } = require('~/server/services/AuthService');
  const { createModels, runAsSystem } = require('@librechat/data-schemas');
  const { PrincipalType, PermissionBits, AccessRoleIds } = require('librechat-data-provider');
  const {
    createKnowledgeService,
    createNativeKnowledgeIndexer,
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
  const rawConfig = (await getAppConfig({ baseOnly: true })).config?.knowledge;
  const knowledge = resolveKnowledgeConfig(rawConfig);
  ensure(
    knowledge?.enabled && knowledge.sync?.enabled && knowledge.agentId === fixtureAgent,
    'CONFIG_ENABLED',
  );
  ensure(
    knowledge.sync.wikiUrl === fixtureUrl && knowledge.sync.spaceId === fixtureSpace,
    'CONFIG_SCOPE',
  );

  const accounts = fs.existsSync(accountsPath)
    ? JSON.parse(fs.readFileSync(accountsPath, 'utf8'))
    : {};
  accounts.banned = {
    email: `sync-banned-${nonce}@boyankb-acceptance.invalid`,
    password: randomBytes(24).toString('hex'),
  };
  for (const name of ['admin', 'authorized', 'denied', 'banned']) {
    accounts[name] ??= {
      email: `sync-${name}@boyankb-acceptance.invalid`,
      password: randomBytes(24).toString('hex'),
    };
    const expectedEmail =
      name === 'banned'
        ? `sync-banned-${nonce}@boyankb-acceptance.invalid`
        : `sync-${name}@boyankb-acceptance.invalid`;
    ensure(accounts[name].email === expectedEmail, 'ACCOUNT_IDENTITY');
    if (!(await db.findUser({ email: accounts[name].email }))) {
      const result = await registerUser(
        {
          ...accounts[name],
          name: `Sync ${name}`,
          username: name === 'banned' ? `sync-ban-${nonce.slice(0, 12)}` : `sync-${name}`,
          confirm_password: accounts[name].password,
        },
        { emailVerified: true },
      );
      ensure(result.status === 200, 'ACCOUNT_CREATE');
    }
  }
  accounts.agentName = 'BoyanKB 同步验收';
  accounts.byokEndpoint = 'DeepSeek';
  fs.writeFileSync(accountsPath, JSON.stringify(accounts), { mode: 0o600 });
  const sessions = {};
  for (const name of ['admin', 'authorized', 'denied', 'banned']) {
    const result = status(
      await request('/api/auth/login', { method: 'POST', body: accounts[name] }),
      200,
      'LOGIN',
    );
    ensure(typeof result.data?.token === 'string', 'LOGIN_TOKEN');
    sessions[name] = { token: result.data.token, user: result.data.user };
  }
  ensure(
    sessions.admin.user.role === 'ADMIN' && sessions.authorized.user.role === 'USER',
    'NATIVE_ROLES',
  );
  let agent = await db.getAgent({ id: fixtureAgent });
  if (!agent) {
    agent = await db.createAgent({
      id: fixtureAgent,
      name: accounts.agentName,
      provider: 'DeepSeek',
      model: 'deepseek-flash',
      author: sessions.admin.user.id,
      tools: [],
    });
  }
  ensure(agent.author.toString() === sessions.admin.user.id, 'NATIVE_OWNER');
  await db.grantPermission(
    PrincipalType.USER,
    new mongoose.Types.ObjectId(sessions.admin.user.id),
    'agent',
    agent._id,
    PermissionBits.VIEW | PermissionBits.EDIT | PermissionBits.DELETE | PermissionBits.SHARE,
    new mongoose.Types.ObjectId(sessions.admin.user.id),
  );
  const permissionPath = `/api/permissions/agent/${agent._id.toString()}`;
  const granted = status(
    await request(permissionPath, {
      method: 'PUT',
      session: sessions.admin,
      body: {
        updated: [
          {
            type: 'user',
            id: sessions.authorized.user.id,
            accessRoleId: AccessRoleIds.AGENT_VIEWER,
          },
          {
            type: 'user',
            id: sessions.banned.user.id,
            accessRoleId: AccessRoleIds.AGENT_VIEWER,
          },
        ],
        removed: [{ type: 'user', id: sessions.denied.user.id }],
        public: false,
      },
    }),
    200,
    'GRANT',
  );
  ensure(Array.isArray(granted.data?.results?.principals), 'GRANT_RESULT');
  status(await request('/api/knowledge/tree'), 401, 'ANONYMOUS_TREE');
  status(await request('/api/knowledge/tree', { session: sessions.denied }), 403, 'DENIED_TREE');
  status(
    await request('/api/knowledge/sync-runs', { session: sessions.authorized }),
    403,
    'USER_RUNS',
  );
  status(
    await request('/api/knowledge/sync-runs', {
      method: 'POST',
      session: sessions.authorized,
      body: { mode: 'full' },
      headers: { 'Idempotency-Key': `unauthorized-${nonce}` },
    }),
    403,
    'USER_ENQUEUE',
  );
  pass('native roles, individual VIEW and HTTP management boundaries');

  const state = { version: 1, visible: true, denied: false, paused: false, rootCalls: 0 };
  const makeNode = (nodeToken, objToken, title, fields = {}) => ({
    space_id: fixtureSpace,
    node_token: nodeToken,
    obj_token: objToken,
    obj_type: 'docx',
    title,
    has_child: false,
    obj_edit_time: String(1750000000 + state.version),
    ...fields,
  });
  const leaf = () => makeNode('fixture_leaf', 'fixture_document', 'AI 营地课程样例');
  const folder = () =>
    makeNode('fixture_folder', 'fixture_overview', '科创教育目录', { has_child: true });
  const blocks = (token) => {
    if (token === 'fixture_partial') {
      return [
        { block_id: 'page', block_type: 1, page: { elements: [] }, children: ['unknown'] },
        { block_id: 'unknown', parent_id: 'page', block_type: 999 },
      ];
    }
    const content =
      token === 'fixture_document'
        ? `人工构造课程第${state.version}版：学生参观机器人实验室，认识人工智能，制作纸板机械臂。`
        : '人工构造企业科创课程目录，涵盖机器人与人工智能营地。';
    return [
      {
        block_id: 'page',
        block_type: 1,
        page: { elements: [] },
        children: ['heading', 'text', ...(token === 'fixture_document' ? ['image'] : [])],
      },
      {
        block_id: 'heading',
        parent_id: 'page',
        block_type: 3,
        heading1: { elements: [{ text_run: { content: '课程目标' } }] },
      },
      {
        block_id: 'text',
        parent_id: 'page',
        block_type: 2,
        text: { elements: [{ text_run: { content } }] },
      },
      ...(token === 'fixture_document'
        ? [
            {
              block_id: 'image',
              parent_id: 'page',
              block_type: 27,
              image: { token: 'fixture_media' },
            },
          ]
        : []),
    ];
  };
  const client = {
    async resolveSpace(url, expected) {
      ensure(url === fixtureUrl && expected === fixtureSpace, 'FAKE_RESOLVE_SCOPE');
      if (state.paused) {
        throw new FeishuError('auth', 'source');
      }
      return { spaceId: fixtureSpace, node: leaf() };
    },
    async listNodes(space, parent, cursor) {
      ensure(space === fixtureSpace, 'FAKE_LIST_SCOPE');
      if (parent === 'fixture_folder') {
        return {
          items: state.visible
            ? [
                makeNode('fixture_alias', 'fixture_document', '营地课程快捷方式', {
                  parent_node_token: 'fixture_folder',
                }),
              ]
            : [],
          hasMore: false,
        };
      }
      ensure(!parent, 'FAKE_ROOT_SCOPE');
      state.rootCalls++;
      if (cursor) {
        ensure(cursor === 'fixture_next', 'FAKE_CURSOR');
        return {
          items: [
            makeNode('fixture_sheet', 'fixture_sheet_object', '待支持表格', { obj_type: 'sheet' }),
            makeNode('fixture_partial_node', 'fixture_partial', '部分可解析资料'),
          ],
          hasMore: false,
        };
      }
      return {
        items: [folder(), ...(state.visible ? [leaf()] : [])],
        hasMore: true,
        pageToken: 'fixture_next',
      };
    },
    async getNode(token) {
      if (!state.visible && ['fixture_leaf', 'fixture_alias'].includes(token)) {
        throw new FeishuError('not_found');
      }
      return leaf();
    },
    async getDocument(token) {
      if (state.denied && token === 'fixture_document') {
        throw new FeishuError('document_denied');
      }
      return {
        document_id: token,
        title:
          {
            fixture_document: 'AI 营地课程样例',
            fixture_overview: '科创教育目录',
            fixture_partial: '部分可解析资料',
          }[token] || '合成资料',
        revision_id: token === 'fixture_document' ? state.version : 1,
      };
    },
    async getBlocks(token) {
      return blocks(token);
    },
    async getMetadata() {
      return { metas: [], failed_list: [] };
    },
    async downloadMedia(token) {
      ensure(token === 'fixture_media', 'FAKE_MEDIA_SCOPE');
      return { buffer: imageBytes, contentType: 'image/png' };
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
    workerId: `fixture-${nonce}`,
  });
  await runAsSystem(() => service.ensureSource());
  const source = await service.source();
  ensure(!source.leaseOwner || source.leaseUntil <= new Date(), 'NO_ACTIVE_WORKER');
  await models.KnowledgeRun.updateMany(
    { sourceId: service.sourceId, status: { $in: ['queued', 'running'] } },
    {
      $set: { status: 'failed', errorCode: 'KNOWLEDGE_SYNC_TEST_RESTART', finishedAt: new Date() },
    },
  );

  async function enqueue(label, mode = 'full', retryRunId) {
    const route = retryRunId
      ? `/api/knowledge/sync-runs/${retryRunId}/retry`
      : '/api/knowledge/sync-runs';
    return status(
      await request(route, {
        method: 'POST',
        session: sessions.admin,
        body: { mode },
        headers: { 'Idempotency-Key': `${label}-${nonce}` },
      }),
      202,
      'ADMIN_ENQUEUE',
    ).data;
  }
  async function sync(label, mode = 'full', retryRunId) {
    const run = await enqueue(label, mode, retryRunId);
    await runAsSystem(() => service.processNext());
    return status(
      await request(`/api/knowledge/sync-runs/${run.id}`, { session: sessions.admin }),
      200,
      'ADMIN_RUN',
    ).data;
  }
  async function documentRecord() {
    const document = await models.KnowledgeDocument.findOne({
      sourceId: service.sourceId,
      objToken: 'fixture_document',
    }).lean();
    ensure(Boolean(document), 'DOCUMENT_RECORD');
    return document;
  }
  async function readDocument(id, revisionId) {
    return request(
      `/api/knowledge/documents/${id}${revisionId ? `/revisions/${revisionId}` : ''}`,
      { session: sessions.authorized },
    );
  }
  async function nativeIds() {
    const response = status(
      await request(`/api/files/agent/${fixtureAgent}`, { session: sessions.admin }),
      200,
      'NATIVE_FILES',
    );
    ensure(Array.isArray(response.data), 'NATIVE_FILES_SHAPE');
    return response.data.map((file) => file.file_id);
  }
  async function blocked(documentId, revisionId, assetId, fileIds) {
    status(await readDocument(documentId), 404, 'OFFLINE_DOCUMENT');
    status(await readDocument(documentId, revisionId), 404, 'OFFLINE_REVISION');
    status(
      await request(`/api/knowledge/assets/${assetId}`, { session: sessions.authorized }),
      404,
      'OFFLINE_ASSET',
    );
    for (const fileId of fileIds) {
      status(
        await request(`/api/files/${fileId}/preview`, { session: sessions.authorized }),
        404,
        'OFFLINE_NATIVE_PREVIEW',
      );
      status(
        await request(`/api/files/download-url/${ownerId}/${fileId}`, { session: sessions.admin }),
        404,
        'OFFLINE_NATIVE_OWNER',
      );
    }
  }

  const first = await enqueue('initial');
  ensure((await enqueue('initial')).id === first.id, 'ENQUEUE_IDEMPOTENCY');
  await runAsSystem(() => service.processNext());
  const initialRun = status(
    await request(`/api/knowledge/sync-runs/${first.id}`, { session: sessions.admin }),
    200,
    'INITIAL_RUN',
  ).data;
  ensure(initialRun.status === 'partial', 'INITIAL_PARTIAL');
  ensure(
    initialRun.counts.nodes === 5 &&
      initialRun.counts.documents === 4 &&
      initialRun.counts.unsupported === 1,
    'SPACE_COVERAGE',
  );
  ensure(state.rootCalls === 2, 'LEAF_TO_SPACE_ENUMERATION');
  const document = await documentRecord();
  const original = status(await readDocument(document.id), 200, 'DOCUMENT_READ').data;
  ensure(original.title === 'AI 营地课程样例', 'CANONICAL_DOCUMENT_TITLE');
  ensure(original.assets.length === 1, 'IMAGE_REFERENCE');
  const originalAsset = original.assets[0].id;
  const initialFiles = await service.activeFileIds();
  ensure(
    initialFiles.length === 2 && (await indexer.verify(initialFiles)),
    'REAL_RAG_VERIFICATION',
  );
  ensure((await nativeIds()).length === 2, 'NATIVE_AGENT_MAPPING');
  const documentRevision = await models.KnowledgeRevision.findOne({
    id: original.revision.id,
  }).lean();
  const oldFileIds = documentRevision.nativeFileIds;
  for (const fileId of oldFileIds) {
    status(
      await request(`/api/files/${fileId}/preview`, { session: sessions.authorized }),
      200,
      'ACTIVE_NATIVE_PREVIEW',
    );
  }
  const firstPage = status(
    await request('/api/knowledge/tree?limit=2', { session: sessions.authorized }),
    200,
    'TREE_PAGE',
  ).data;
  ensure(firstPage.items.length === 2 && firstPage.nextCursor, 'TREE_CURSOR');
  const secondPage = status(
    await request(`/api/knowledge/tree?limit=2&cursor=${firstPage.nextCursor}`, {
      session: sessions.authorized,
    }),
    200,
    'TREE_NEXT',
  ).data;
  ensure(secondPage.items.length === 2 && !secondPage.nextCursor, 'TREE_COMPLETE');
  const allNodes = [...firstPage.items, ...secondPage.items];
  const parent = allNodes.find((item) => item.hasChildren);
  ensure(Boolean(parent), 'FOLDER_NODE');
  const child = status(
    await request(`/api/knowledge/tree?parentId=${parent.id}`, { session: sessions.authorized }),
    200,
    'TREE_CHILD',
  ).data;
  ensure(
    child.items.length === 1 && child.items[0].documentId === document.id,
    'SHORTCUT_DEDUPLICATION',
  );
  ensure(child.items[0].title === '营地课程快捷方式', 'SHORTCUT_DIRECTORY_TITLE');
  ensure(
    !JSON.stringify({ original, firstPage, secondPage, initialRun }).includes('fixture_document'),
    'SOURCE_TOKEN_REDACTION',
  );
  pass('leaf-to-space recursion, native indexing, idempotency and paginated directory');

  status(
    await request(`/api/knowledge/documents/${document.id}`, { session: sessions.denied }),
    403,
    'DENIED_DOCUMENT',
  );
  status(
    await request(`/api/knowledge/assets/${originalAsset}`, { session: sessions.denied }),
    403,
    'DENIED_ASSET',
  );
  const asset = status(
    await request(`/api/knowledge/assets/${originalAsset}`, { session: sessions.authorized }),
    200,
    'ASSET_READ',
  );
  ensure(
    asset.bytes.equals(imageBytes) && asset.headers.get('content-type').startsWith('image/png'),
    'ASSET_CONTENT',
  );
  const range = status(
    await request(`/api/knowledge/assets/${originalAsset}`, {
      session: sessions.authorized,
      headers: { Range: 'bytes=1-5' },
    }),
    206,
    'ASSET_RANGE',
  );
  ensure(
    range.bytes.equals(imageBytes.subarray(1, 6)) &&
      range.headers.get('content-range') === `bytes 1-5/${imageBytes.length}`,
    'ASSET_RANGE_CONTENT',
  );
  status(
    await request(`/api/knowledge/assets/${originalAsset}`, {
      session: sessions.authorized,
      headers: { Range: 'bytes=99999-' },
    }),
    416,
    'ASSET_RANGE_INVALID',
  );
  const head = status(
    await request(`/api/knowledge/assets/${originalAsset}`, {
      method: 'HEAD',
      session: sessions.authorized,
    }),
    200,
    'ASSET_HEAD',
  );
  ensure(head.bytes.length === 0, 'ASSET_HEAD_BODY');
  pass('authorized reading, image streaming, Range and resource access isolation');

  const beforeRepeat = await models.File.countDocuments({ context: 'knowledge' });
  await sync('unchanged', 'incremental');
  ensure((await documentRecord()).activeRevisionId === original.revision.id, 'STABLE_REVISION');
  ensure(
    (await models.File.countDocuments({ context: 'knowledge' })) === beforeRepeat,
    'STABLE_NATIVE_FILE',
  );
  state.version = 2;
  await sync('updated', 'incremental');
  const updated = status(await readDocument(document.id), 200, 'UPDATED_DOCUMENT').data;
  ensure(updated.revision.id !== original.revision.id, 'REVISION_SWITCH');
  status(await readDocument(document.id, original.revision.id), 200, 'HISTORICAL_REVISION');
  const updatedRevision = await models.KnowledgeRevision.findOne({
    id: updated.revision.id,
  }).lean();
  ensure(
    updatedRevision.nativeFileIds.every((fileId) => !oldFileIds.includes(fileId)),
    'NATIVE_REVISION_SWITCH',
  );
  const currentFiles = await nativeIds();
  ensure(
    oldFileIds.every((fileId) => !currentFiles.includes(fileId)),
    'OLD_NATIVE_MAPPING_REMOVED',
  );
  for (const fileId of oldFileIds) {
    status(
      await request(`/api/files/${fileId}/preview`, { session: sessions.admin }),
      404,
      'OLD_NATIVE_OWNER_BLOCKED',
    );
  }
  pass('stable snapshot reuse, atomic active revision and historical reading');

  state.denied = true;
  await sync('document-denied');
  await blocked(
    document.id,
    updated.revision.id,
    updated.assets[0].id,
    updatedRevision.nativeFileIds,
  );
  state.denied = false;
  await sync('permission-restored');
  status(await readDocument(document.id), 200, 'PERMISSION_RESTORED');
  state.visible = false;
  await sync('removed');
  await blocked(document.id, original.revision.id, originalAsset, updatedRevision.nativeFileIds);
  state.visible = true;
  await sync('returned');
  status(await readDocument(document.id), 200, 'DOCUMENT_RETURNED');
  pass('confirmed document revocation and removal close assets, citations and native file routes');

  state.paused = true;
  const pausedRun = await sync('source-paused');
  ensure(pausedRun.status === 'failed', 'PAUSED_RUN');
  const pausedTree = status(
    await request('/api/knowledge/tree', { session: sessions.authorized }),
    200,
    'PAUSED_TREE',
  ).data;
  ensure(
    pausedTree.sourceStatus === 'paused' && pausedTree.items.length === 0,
    'PAUSED_TREE_HIDDEN',
  );
  status(await readDocument(document.id), 503, 'PAUSED_DOCUMENT');
  ensure(
    (await db.getAgent({ id: fixtureAgent })).tool_resources.file_search.file_ids.length === 0,
    'PAUSED_NATIVE_MAPPING',
  );
  state.paused = false;
  const recoveryRun = await enqueue('recovery', 'full', pausedRun.id);
  await runAsSystem(() => service.processNext({ maxSteps: 1 }));
  const recoveringTree = status(
    await request('/api/knowledge/tree', { session: sessions.authorized }),
    200,
    'RECOVERING_TREE',
  ).data;
  ensure(recoveringTree.items.length === 0, 'RECOVERING_METADATA_HIDDEN');
  await runAsSystem(() => service.processNext());
  status(await readDocument(document.id), 200, 'RECOVERED_DOCUMENT');
  ensure((await service.activeFileIds()).length === 2, 'RECOVERED_NATIVE_MAPPING');
  const finalRun = status(
    await request(`/api/knowledge/sync-runs/${recoveryRun.id}`, { session: sessions.admin }),
    200,
    'RECOVERED_RUN',
  ).data;
  ensure(finalRun.status === 'partial', 'RECOVERED_PARTIAL_FORMATS');
  status(
    await request('/api/knowledge/sync-runs?limit=2', { session: sessions.admin }),
    200,
    'RUN_PAGINATION',
  );
  pass('source pause, epoch-protected recovery and native ADMIN retry');

  const finalDocument = status(await readDocument(document.id), 200, 'BROWSER_DOCUMENT').data;
  const protectedPaths = [
    '/api/knowledge/tree',
    `/api/knowledge/documents/${document.id}`,
    `/api/knowledge/documents/${document.id}/revisions/${original.revision.id}`,
    `/api/knowledge/assets/${finalDocument.assets[0].id}`,
  ];
  status(
    await request(permissionPath, {
      method: 'PUT',
      session: sessions.admin,
      body: {
        updated: [],
        removed: [{ type: 'user', id: sessions.authorized.user.id }],
        public: false,
      },
    }),
    200,
    'REVOKE_VIEW',
  );
  ensure(
    (await models.Session.countDocuments({ user: sessions.authorized.user.id })) === 0,
    'REVOKED_REFRESH_SESSIONS',
  );
  for (const route of protectedPaths) {
    status(await request(route, { session: sessions.authorized }), 403, 'REVOKED_OLD_TOKEN');
  }
  status(
    await request(permissionPath, {
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
        removed: [],
        public: false,
      },
    }),
    200,
    'RESTORE_VIEW',
  );
  for (const route of protectedPaths) {
    status(await request(route, { session: sessions.authorized }), 200, 'RESTORED_VIEW');
    status(await request(route, { session: sessions.banned }), 200, 'BEFORE_BAN');
  }
  const banExit = await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ['config/ban-user.js', accounts.banned.email, '60000'], {
      cwd: process.cwd(),
      env: process.env,
      stdio: 'ignore',
      timeout: 30000,
    });
    child.once('error', reject);
    child.once('exit', resolve);
  });
  ensure(banExit === 0, 'NATIVE_BAN_COMMAND');
  ensure(
    (await models.Session.countDocuments({ user: sessions.banned.user.id })) === 0,
    'BANNED_REFRESH_SESSIONS',
  );
  for (const route of protectedPaths) {
    status(await request(route, { session: sessions.banned }), 403, 'BANNED_OLD_TOKEN');
    status(await request(route, { session: sessions.authorized }), 200, 'SAME_IP_UNAFFECTED');
  }
  pass(
    'VIEW revocation and native ban reject old tokens on directory, documents, citations and assets',
  );

  fs.writeFileSync(
    browserPath,
    JSON.stringify({
      documentId: document.id,
      revisionId: finalDocument.revision.id,
      oldRevisionId: original.revision.id,
      assetId: finalDocument.assets[0].id,
      parentNodeId: parent.id,
      runId: recoveryRun.id,
      agentName: accounts.agentName,
      partialDocumentId: allNodes.find((item) => item.status === 'partial')?.documentId,
      unsupportedDocumentId: allNodes.find((item) => item.status === 'unsupported')?.documentId,
    }),
    { mode: 0o600 },
  );
  report.counts = { nodes: 5, documents: 4, activeFiles: 2, scenarios: report.checks.length };
  report.passed = true;
}

main()
  .catch((error) => {
    report.errorCode = safeCode(error);
    console.error(report.errorCode);
    process.exitCode = 1;
  })
  .finally(async () => {
    report.at = new Date().toISOString();
    if (
      process.env.BOYANKB_TEST_INSTANCE === 'boyankb-librechat-sync-test' &&
      process.env.NODE_ENV === 'test'
    ) {
      fs.writeFileSync(reportPath, JSON.stringify(report, null, 2), { mode: 0o600 });
    }
    await mongoose?.disconnect().catch(() => {});
    process.exit(report.passed ? 0 : 1);
  });
