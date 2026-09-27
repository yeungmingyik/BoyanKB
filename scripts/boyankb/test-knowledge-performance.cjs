const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { createServer } = require('node:http');
const { randomBytes, randomUUID, createHash } = require('node:crypto');
const { performance } = require('node:perf_hooks');
const { setTimeout: delay } = require('node:timers/promises');

const fixtureAgent = 'agent_sync_acceptance';
const fixtureSpace = 'space_sync_acceptance';
const fixtureProvider = 'BoyanFixture';
const fixtureModel = 'boyan-slow-test';
const question = '星帆机器人课程每班最多多少名学生？';
const reportPath = '/app/data/knowledge-performance-results.json';
const nonce = randomUUID().replaceAll('-', '');
const runId = process.env.BOYANKB_PERFORMANCE_RUN_ID ?? randomUUID();
const users = Number(process.env.BOYANKB_PERFORMANCE_USERS ?? 10);
const rounds = Number(process.env.BOYANKB_PERFORMANCE_ROUNDS ?? 3);
const mode = process.env.BOYANKB_PERFORMANCE_MODEL_MODE ?? 'Stub';
const selectedScenario = process.env.BOYANKB_PERFORMANCE_SCENARIO ?? 'All';
const real = mode === 'Real';
const endpoint = real ? 'DeepSeek' : fixtureProvider;
const ownedUsers = [];
const ownedTurns = [];
const report = {
  runId,
  passed: false,
  cleaned: false,
  validationTarget: 'isolated-synthetic',
  modelMode: mode,
  selectedScenario,
  concurrentUsers: users,
  readSearchRounds: rounds,
  qaRounds: 1,
  scenarios: [],
  checks: [],
  startedAt: new Date().toISOString(),
  scope: 'local-api-synthetic-workload',
  timing: {
    readingAndSearch: 'request-to-response',
    qa: 'request-to-verified-final',
    percentiles: 'successful-requests-only',
  },
  thresholdsMs: { reading: 3000, keyword: 5000, semantic: 8000, qa: 15000 },
  maximumErrorRate: 0,
  sourceSnapshotExcludedFields: ['sources.updatedAt'],
};
let model = fixtureModel;
let apiKey;
let mongoose;
let models;
let runAsSystem;
let admin;
let permissionPath;
let provider;
let reportAllowed = false;
let validated = false;
let source;
let fixture;
let initialSnapshot;
let initialSnapshotState;
let providerRequests = 0;
let providerActive = 0;
let providerPeak = 0;
let providerError;
let qaRequests = 0;

function ensure(condition, code) {
  if (!condition) throw new Error(`KNOWLEDGE_PERFORMANCE_${code}`);
}

function safeCode(error) {
  if (/^KNOWLEDGE_PERFORMANCE_[A-Z0-9_]+$/.test(error?.message ?? '')) return error.message;
  if (['TimeoutError', 'AbortError'].includes(error?.name)) return 'KNOWLEDGE_PERFORMANCE_TIMEOUT';
  return 'KNOWLEDGE_PERFORMANCE_FAILED';
}

function headers(session) {
  return {
    Origin: process.env.DOMAIN_CLIENT,
    'Content-Type': 'application/json',
    'X-LibreChat-Generation-Protocol': '2',
    'User-Agent':
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36',
    ...(session ? { Authorization: `Bearer ${session.token}` } : {}),
  };
}

async function request(route, { session, method = 'GET', body } = {}) {
  ensure(route.startsWith('/api/') && !route.includes('://'), 'INTERNAL_ROUTE');
  const response = await fetch(`http://127.0.0.1:3080${route}`, {
    method,
    redirect: 'manual',
    headers: headers(session),
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal: AbortSignal.timeout(90000),
  });
  const text = await response.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = undefined;
  }
  return { status: response.status, data };
}

function status(response, expected, code) {
  ensure(response.status === expected, `${code}_HTTP_${response.status}`);
  return response.data;
}

function messageText(message) {
  return (message.content ?? [])
    .filter((part) => part.type === 'text')
    .map((part) => (typeof part.text === 'string' ? part.text : (part.text?.value ?? '')))
    .join('\n');
}

function percentile(values, fraction) {
  const ordered = values.toSorted((a, b) => a - b);
  return ordered[Math.max(0, Math.ceil(ordered.length * fraction) - 1)] ?? 0;
}

async function snapshot(capture = false) {
  const [sources, nodes, documents, revisions, agents] = await runAsSystem(() =>
    Promise.all([
      models.KnowledgeSource.find({}).sort({ id: 1 }).lean(),
      models.KnowledgeNode.find({}).sort({ id: 1 }).lean(),
      models.KnowledgeDocument.find({}).sort({ id: 1 }).lean(),
      models.KnowledgeRevision.find({}).sort({ id: 1 }).lean(),
      models.Agent.find({ id: fixtureAgent }).select('id tool_resources').lean(),
    ]),
  );
  const state = { sources, nodes, documents, revisions, agents };
  if (capture) initialSnapshotState = state;
  else if (initialSnapshotState) {
    report.sourceChangedFields = Object.keys(state).flatMap((collection) =>
      state[collection].flatMap((record, index) => {
        const previous = initialSnapshotState[collection][index];
        return [...new Set([...Object.keys(record), ...Object.keys(previous ?? {})])]
          .filter((key) => JSON.stringify(record[key]) !== JSON.stringify(previous?.[key]))
          .map((field) => ({ collection, record: index, field }));
      }),
    );
  }
  const semanticState = {
    ...state,
    sources: sources.map((item) => {
      const sourceState = { ...item };
      delete sourceState.updatedAt;
      return sourceState;
    }),
  };
  return createHash('sha256').update(JSON.stringify(semanticState)).digest('hex');
}

async function grant(session) {
  const { AccessRoleIds } = require('librechat-data-provider');
  status(
    await request(permissionPath, {
      method: 'PUT',
      session: admin,
      body: {
        updated: [{ type: 'user', id: session.user.id, accessRoleId: AccessRoleIds.AGENT_VIEWER }],
        removed: [],
        public: false,
      },
    }),
    200,
    'GRANT',
  );
}

async function newUser(index, authorized) {
  const { registerUser } = require('~/server/services/AuthService');
  const email = `performance-${index}-${nonce}@boyankb-acceptance.invalid`;
  const password = randomBytes(24).toString('hex');
  const created = await registerUser(
    {
      email,
      password,
      confirm_password: password,
      name: `Performance ${index}`,
      username: `performance-${index}-${nonce}`,
    },
    { emailVerified: true },
  );
  ensure(created.status === 200, 'CREATE_USER');
  const user = await models.User.findOne({ email }).select('_id role').lean();
  ensure(user?.role === 'USER', 'USER_ROLE');
  const owned = { email, index, id: user._id.toString() };
  ownedUsers.push(owned);
  const login = status(
    await request('/api/auth/login', { method: 'POST', body: { email, password } }),
    200,
    'LOGIN',
  );
  ensure(login.user?.id === owned.id && typeof login.token === 'string', 'LOGIN_IDENTITY');
  const session = { user: login.user, token: login.token };
  owned.session = session;
  if (authorized) await grant(session);
  if (authorized && real) {
    status(
      await request('/api/keys', {
        session,
        method: 'PUT',
        body: {
          name: endpoint,
          value: JSON.stringify({ apiKey, models: [model] }),
          expiresAt: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
        },
      }),
      201,
      'KEY_WRITE',
    );
    const saved = await models.Key.findOne({ userId: owned.id, name: endpoint })
      .select('value')
      .lean();
    ensure(typeof saved?.value === 'string' && !saved.value.includes(apiKey), 'KEY_ENCRYPTION');
  }
  return session;
}

async function startProvider() {
  provider = createServer(async (req, res) => {
    let active = false;
    try {
      ensure(req.method === 'POST' && req.url === '/v1/chat/completions', 'PROVIDER_ROUTE');
      ensure(req.headers.authorization === 'Bearer fixture-key', 'PROVIDER_KEY');
      let text = '';
      for await (const chunk of req) {
        text += chunk;
        ensure(Buffer.byteLength(text) < 100000, 'PROVIDER_INPUT_LIMIT');
      }
      const body = JSON.parse(text);
      ensure(body.model === fixtureModel && body.stream === true, 'PROVIDER_REQUEST');
      ensure(
        text.includes('Knowledge excerpts (JSON data):') && text.includes('每班最多16名学生'),
        'PROVIDER_CONTEXT',
      );
      ensure(++providerRequests <= users, 'PROVIDER_REQUEST_LIMIT');
      providerActive += 1;
      active = true;
      providerPeak = Math.max(providerPeak, providerActive);
      const completion = {
        id: `chatcmpl-${randomUUID()}`,
        created: Math.floor(Date.now() / 1000),
        model: fixtureModel,
        object: 'chat.completion.chunk',
      };
      const emit = (delta, finish_reason = null) =>
        res.write(
          `data: ${JSON.stringify({ ...completion, choices: [{ index: 0, delta, finish_reason }] })}\n\n`,
        );
      res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
      res.flushHeaders();
      emit({ role: 'assistant', content: '' });
      await delay(500);
      emit({ content: '每班最多16名学生。[1]' });
      emit({}, 'stop');
      res.end('data: [DONE]\n\n');
    } catch (error) {
      providerError = safeCode(error);
      if (!res.headersSent) res.writeHead(400);
      res.end();
    } finally {
      if (active) providerActive -= 1;
    }
  });
  await new Promise((resolve, reject) => {
    provider.once('error', reject);
    provider.listen(19091, '127.0.0.1', resolve);
  });
}

async function readAnswer(turn, session) {
  const response = await fetch(
    `http://127.0.0.1:3080/api/agents/chat/stream/${turn.streamId}?generationCreatedAt=${turn.generationCreatedAt}&resume=true`,
    {
      headers: headers(session),
      redirect: 'manual',
      signal: AbortSignal.timeout(90000),
    },
  );
  ensure(
    response.status === 200 && response.headers.get('content-type')?.includes('text/event-stream'),
    `STREAM_HTTP_${response.status}`,
  );
  const reader = response.body.getReader();
  let buffer = '';
  let bytes = 0;
  const decoder = new TextDecoder();
  try {
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      bytes += next.value.byteLength;
      ensure(bytes < 2000000, 'STREAM_SIZE');
      buffer = (buffer + decoder.decode(next.value, { stream: true })).replaceAll('\r\n', '\n');
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const payload = frame
          .split('\n')
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trimStart())
          .join('\n');
        if (!payload) continue;
        const event = JSON.parse(payload);
        if (event.error || event.responseMessage?.error) {
          const codes = JSON.stringify(event).match(/KNOWLEDGE_[A-Z_]+/g) ?? [];
          throw new Error(`KNOWLEDGE_PERFORMANCE_${codes[0] ?? 'MODEL_RESPONSE_ERROR'}`);
        }
        if (event.final) {
          ensure(event.responseMessage?.metadata?.knowledge?.verified === true, 'VERIFIED_FINAL');
          return event.responseMessage;
        }
      }
    }
    throw new Error('KNOWLEDGE_PERFORMANCE_FINAL_MISSING');
  } finally {
    await reader.cancel().catch(() => {});
  }
}

async function answer(session) {
  ensure(++qaRequests <= users, 'QA_REQUEST_LIMIT');
  const messageId = randomUUID();
  const startedAt = performance.now();
  const turn = status(
    await request(`/api/agents/chat/${endpoint}`, {
      method: 'POST',
      session,
      body: {
        text: question,
        sender: 'User',
        clientTimestamp: new Date().toISOString(),
        isCreatedByUser: true,
        parentMessageId: '00000000-0000-0000-0000-000000000000',
        conversationId: 'new',
        messageId,
        responseMessageId: `${messageId}_response`,
        endpoint,
        endpointType: 'custom',
        model,
        max_tokens: 256,
        thinking: false,
        isTemporary: false,
        isRegenerate: false,
        error: false,
      },
    }),
    200,
    'QA_START',
  );
  ensure(turn.status === 'started' && /^[a-f0-9-]{36}$/.test(turn.streamId), 'QA_START_IDENTITY');
  ownedTurns.push({ ...turn, session });
  const response = await readAnswer(turn, session);
  const elapsedMs = Math.round(performance.now() - startedAt);
  const text = messageText(response);
  ensure(response.conversationId === turn.conversationId && text.includes('16'), 'QA_ANSWER');
  if (real) {
    ensure(
      response.metadata.usage &&
        Number(response.metadata.usage.output) > 0 &&
        Number(response.metadata.usage.output) <= 256,
      'REAL_USAGE_LIMIT',
    );
  }
  const citations = response.metadata.knowledge.citations;
  ensure(
    citations.length > 0 &&
      citations.every((citation) =>
        [...fixture.documents, ...fixture.directories].some(
          (document) =>
            document.documentId === citation.documentId &&
            document.revisionId === citation.revisionId &&
            document.blockIds.includes(citation.blockId),
        ),
      ),
    'QA_CITATION',
  );
  ensure(
    citations.every((citation) =>
      text.includes(
        `/knowledge/documents/${citation.documentId}/revisions/${citation.revisionId}#block-${citation.blockId}`,
      ),
    ),
    'QA_CITATION_LINK',
  );
  const saved = status(
    await request(`/api/messages/${turn.conversationId}`, { session }),
    200,
    'QA_PERSISTED',
  );
  ensure(
    saved.some(
      (message) =>
        message.messageId === response.messageId &&
        messageText(message) === text &&
        message.metadata?.knowledge?.verified === true,
    ),
    'QA_PERSISTED_CONTENT',
  );
  ensure(
    await models.Conversation.exists({
      conversationId: turn.conversationId,
      user: session.user.id,
    }),
    'QA_OWNER',
  );
  ensure(
    !(await models.Message.exists({
      conversationId: turn.conversationId,
      user: { $ne: session.user.id },
    })),
    'QA_MESSAGE_OWNER',
  );
  return {
    elapsedMs,
    conversationId: turn.conversationId,
    citations: citations.length,
    ...(response.metadata.usage
      ? {
          usage: {
            input: Number(response.metadata.usage.input) || 0,
            output: Number(response.metadata.usage.output) || 0,
          },
        }
      : {}),
  };
}

async function scenario(name, sessions, operation, count) {
  const entries = [];
  const startedAt = performance.now();
  await Promise.all(
    sessions.map(async (session, index) => {
      for (let round = 0; round < count; round += 1) {
        const started = performance.now();
        const entry = { user: index + 1, round: round + 1, passed: false };
        try {
          const detail = await operation(session, index);
          Object.assign(entry, detail);
          entry.passed = true;
        } catch (error) {
          entry.errorCode = safeCode(error);
        }
        entry.elapsedMs ??= Math.round(performance.now() - started);
        entries.push(entry);
      }
    }),
  );
  const durationMs = Math.round(performance.now() - startedAt);
  const successes = entries.filter((entry) => entry.passed);
  const timings = successes.map((entry) => entry.elapsedMs);
  const result = {
    name,
    concurrency: sessions.length,
    requests: entries.length,
    successful: successes.length,
    failed: entries.length - successes.length,
    errorRate: (entries.length - successes.length) / entries.length,
    p50Ms: percentile(timings, 0.5),
    p95Ms: percentile(timings, 0.95),
    maxMs: Math.max(0, ...timings),
    durationMs,
    throughputPerSecond: Number((entries.length / (durationMs / 1000)).toFixed(2)),
    passed:
      successes.length === entries.length && percentile(timings, 0.95) <= report.thresholdsMs[name],
    entries,
  };
  report.scenarios.push(result);
  process.stdout.write(
    `${result.passed ? 'PASS' : 'FAIL'} ${name} ${result.successful}/${result.requests} p95=${result.p95Ms}ms\n`,
  );
  return result;
}

async function cleanup() {
  for (const turn of ownedTurns) {
    await request('/api/agents/chat/abort', {
      session: turn.session,
      method: 'POST',
      body: { streamId: turn.streamId, generationCreatedAt: turn.generationCreatedAt },
    }).catch(() => {});
  }
  if (provider?.listening) {
    provider.closeAllConnections();
    await new Promise((resolve) => provider.close(resolve));
  }
  if (validated && ownedUsers.length) {
    await delay(1500);
    for (const owned of ownedUsers) {
      ensure(
        owned.email === `performance-${owned.index}-${nonce}@boyankb-acceptance.invalid`,
        'CLEANUP_IDENTITY',
      );
      await runAsSystem(async () => {
        for (const name of ['Session', 'Message', 'Conversation', 'Transaction', 'Balance'])
          await models[name].deleteMany({ user: owned.id });
        await models.Key.deleteMany({ userId: owned.id });
        await models.AclEntry.deleteMany({ principalType: 'user', principalId: owned.id });
        await models.User.deleteOne({ _id: owned.id, email: owned.email });
      });
    }
    const ids = ownedUsers.map((owned) => owned.id);
    const counts = await Promise.all([
      models.User.countDocuments({ _id: { $in: ids } }),
      ...['Session', 'Message', 'Conversation', 'Transaction', 'Balance'].map((name) =>
        models[name].countDocuments({ user: { $in: ids } }),
      ),
      models.Key.countDocuments({ userId: { $in: ids } }),
      models.AclEntry.countDocuments({ principalType: 'user', principalId: { $in: ids } }),
    ]);
    ensure(
      counts.every((count) => count === 0),
      'CLEANUP_ROWS',
    );
    report.cleanedUsers = ownedUsers.length;
  }
  if (initialSnapshot) {
    report.sourceUnchanged = initialSnapshot === (await snapshot());
    ensure(report.sourceUnchanged, 'SOURCE_CHANGED');
  }
  report.cleaned = true;
}

async function main() {
  ensure(
    [5, 10, 20].includes(users) && Number.isInteger(rounds) && rounds >= 1 && rounds <= 5,
    'LIMITS',
  );
  ensure(['Stub', 'Real'].includes(mode) && (!real || users <= 10), 'MODEL_MODE');
  ensure(['All', 'Qa'].includes(selectedScenario), 'SCENARIO');
  ensure(
    process.env.NODE_ENV === 'test' &&
      process.env.BOYANKB_TEST_INSTANCE === 'boyankb-librechat-sync-test',
    'INSTANCE',
  );
  ensure(
    process.env.BOYANKB_KNOWLEDGE_AGENT_ID === fixtureAgent &&
      process.env.RAG_API_URL === 'http://rag:8000',
    'AGENT',
  );
  ensure(
    process.env.FEISHU_APP_ID === 'fixture_app' &&
      process.env.FEISHU_APP_SECRET === 'fixture_secret',
    'SYNTHETIC_CREDENTIALS',
  );
  ensure(
    !process.env.EMAIL_HOST && !process.env.EMAIL_SERVICE && !process.env.MAILGUN_API_KEY,
    'EMAIL_DISABLED',
  );
  reportAllowed = true;
  require('module-alias')({ base: path.resolve(process.cwd(), 'api') });
  mongoose = require('mongoose');
  const schemas = require('@librechat/data-schemas');
  schemas.logger.silent = true;
  runAsSystem = schemas.runAsSystem;
  await require('~/db').connectDb();
  models = schemas.createModels(mongoose);
  ensure(
    (await models.User.countDocuments({ email: { $not: /@boyankb-acceptance\.invalid$/ } })) === 0,
    'SYNTHETIC_DATABASE',
  );
  ensure(
    (await models.Agent.countDocuments({ id: { $ne: fixtureAgent } })) === 0,
    'ISOLATED_AGENTS',
  );
  ensure(
    (await models.KnowledgeSource.countDocuments({ agentId: { $ne: fixtureAgent } })) === 0,
    'ISOLATED_SOURCES',
  );
  const config = await require('~/server/services/Config').getAppConfig({ baseOnly: true });
  const knowledge = require('@librechat/api').resolveKnowledgeConfig(config.config?.knowledge);
  ensure(
    knowledge?.enabled &&
      knowledge.sync?.enabled &&
      knowledge.agentId === fixtureAgent &&
      knowledge.sync.spaceId === fixtureSpace &&
      knowledge.sync.wikiUrl === 'https://fixture.feishu.cn/wiki/fixture_leaf',
    'CONFIG_SCOPE',
  );
  source = await runAsSystem(() =>
    models.KnowledgeSource.findOne({ agentId: fixtureAgent, spaceId: fixtureSpace }).lean(),
  );
  ensure(source?.health === 'healthy' && source.enabled === true, 'SOURCE_READY');
  ensure(
    !(await models.KnowledgeRun.exists({
      sourceId: source.id,
      status: { $in: ['queued', 'running'] },
    })),
    'SOURCE_IDLE',
  );
  fixture = JSON.parse(fs.readFileSync('/app/data/qa-fixture.json', 'utf8'));
  ensure(
    fixture.agentId === fixtureAgent &&
      fixture.documents?.length === 7 &&
      fixture.directories?.length === 6,
    'FIXTURE_SCOPE',
  );
  ensure(
    fixture.documents.some((document) => document.fixtureId === 'school-course'),
    'FIXTURE_SCHOOL',
  );
  const allowedIds = [...fixture.documents, ...fixture.directories].map(
    (document) => document.documentId,
  );
  ensure(
    (await runAsSystem(() =>
      models.KnowledgeDocument.countDocuments({
        sourceId: source.id,
        status: 'published',
        activeRevisionId: { $exists: true, $ne: null },
        id: { $nin: allowedIds },
      }),
    )) === 0,
    'FIXTURE_PUBLISHED_SCOPE',
  );
  const endpointConfig = config.config?.endpoints?.custom?.find((entry) => entry.name === endpoint);
  if (real) {
    ensure(
      endpointConfig?.apiKey === 'user_provided' &&
        /^https:\/\/api\.deepseek\.com(?:\/v1)?\/?$/.test(endpointConfig.baseURL) &&
        endpointConfig.titleConvo === false &&
        endpointConfig.addParams?.maxRetries === 0 &&
        endpointConfig.addParams?.thinking?.type === 'disabled',
      'REAL_REQUEST_BOUND',
    );
    ensure(endpointConfig.models.fetch === false, 'REAL_MODEL_DISCOVERY_DISABLED');
    const credentials = Object.fromEntries(
      fs
        .readFileSync('/app/data/model-test.env', 'utf8')
        .split(/\r?\n/)
        .filter((line) => /^[A-Z_]+=/.test(line))
        .map((line) => {
          const split = line.indexOf('=');
          return [line.slice(0, split), line.slice(split + 1).trim()];
        }),
    );
    apiKey = credentials.DEEPSEEK_API_KEY;
    model = credentials.DEEPSEEK_TEST_MODEL;
    ensure(
      typeof apiKey === 'string' &&
        apiKey.length >= 16 &&
        /^[a-zA-Z0-9._-]{1,128}$/.test(model ?? ''),
      'REAL_CREDENTIALS',
    );
    report.realModelLimits = {
      conversationRequests: users,
      retriesPerRequest: 0,
      maxOutputTokensPerRequest: 256,
      maximumOutputTokens: users * 256,
      maximumKnowledgeContextChars:
        require('librechat-data-provider').knowledgeSearchConfigSchema.parse(knowledge.search ?? {})
          .maxContextChars,
      model,
      personalKeys: true,
    };
  } else {
    ensure(
      endpointConfig?.baseURL === 'http://127.0.0.1:19091/v1' &&
        endpointConfig.apiKey === 'fixture-key' &&
        endpointConfig.models.default.includes(fixtureModel) &&
        endpointConfig.models.fetch === false &&
        endpointConfig.titleConvo === false,
      'STUB_CONFIG',
    );
    report.stubModel = { protocol: 'openai-chat-completions', latencyMs: 500, model: fixtureModel };
  }
  initialSnapshot = await snapshot(true);
  const accounts = JSON.parse(fs.readFileSync('/app/data/browser-smoke-accounts.json', 'utf8'));
  ensure(accounts.admin?.email === 'sync-admin@boyankb-acceptance.invalid', 'ADMIN_IDENTITY');
  const login = status(
    await request('/api/auth/login', { method: 'POST', body: accounts.admin }),
    200,
    'ADMIN_LOGIN',
  );
  ensure(login.user?.role === 'ADMIN', 'ADMIN_ROLE');
  admin = { user: login.user, token: login.token };
  const agent = await models.Agent.findOne({ id: fixtureAgent }).select('_id author').lean();
  ensure(agent?.author?.toString() === admin.user.id, 'AGENT_OWNER');
  permissionPath = `/api/permissions/agent/${agent._id.toString()}`;
  validated = true;
  report.environment = {
    node: process.version,
    platform: process.platform,
    architecture: process.arch,
    cpus: os.cpus().length,
    memoryBytes: os.totalmem(),
    sourceDocuments: fixture.documents.length,
    sourceDirectories: fixture.directories.length,
    sourceSnapshotSha256: initialSnapshot,
  };
  const sessions = [];
  for (let index = 0; index < users; index += 1) sessions.push(await newUser(index, true));
  const denied = await newUser('denied', false);
  const document = fixture.documents.find((item) => item.fixtureId === 'school-course');
  const route = `/api/knowledge/documents/${document.documentId}/revisions/${document.revisionId}`;
  ensure([401, 403].includes((await request(route)).status), 'ANONYMOUS_READING');
  ensure([401, 403].includes((await request(route, { session: denied })).status), 'DENIED_READING');
  ensure(
    [401, 403].includes(
      (
        await request('/api/knowledge/search', {
          session: denied,
          method: 'POST',
          body: { query: question, mode: 'semantic' },
        })
      ).status,
    ),
    'DENIED_SEARCH',
  );
  report.checks.push('anonymous and missing VIEW denied');
  if (!real && selectedScenario === 'All') {
    await scenario(
      'reading',
      sessions,
      async (session) => {
        const content = status(await request(route, { session }), 200, 'READ');
        ensure(
          content.id === document.documentId &&
            content.revision?.id === document.revisionId &&
            Array.isArray(content.blocks),
          'READ_CONTENT',
        );
      },
      rounds,
    );
    for (const searchMode of ['keyword', 'semantic']) {
      await scenario(
        searchMode,
        sessions,
        async (session) => {
          const found = status(
            await request('/api/knowledge/search', {
              session,
              method: 'POST',
              body: { query: question, mode: searchMode, limit: 10 },
            }),
            200,
            'SEARCH',
          );
          ensure(
            found.items?.length > 0 &&
              found.items.some((item) => item.documentId === document.documentId) &&
              found.items.every((item) => allowedIds.includes(item.documentId)),
            'SEARCH_SCOPE',
          );
        },
        rounds,
      );
    }
  }
  if (!real) await startProvider();
  const qa = await scenario('qa', sessions, answer, 1);
  for (const entry of qa.entries.filter((item) => item.passed)) {
    const other = sessions[entry.user % sessions.length];
    status(
      await request(`/api/convos/${entry.conversationId}`, { session: other }),
      404,
      'FOREIGN_CONVERSATION',
    );
    status(
      await request(`/api/messages/${entry.conversationId}`, { session: other }),
      404,
      'FOREIGN_MESSAGES',
    );
  }
  ensure(!providerError, 'PROVIDER_FAILED');
  if (!real) ensure(providerRequests === users, 'STUB_REQUEST_COUNT');
  report.checks.push('cross-user conversations and messages denied');
  report.provider = real
    ? { endpoint, external: true, retriesPerRequest: 0 }
    : {
        endpoint,
        external: false,
        requests: providerRequests,
        peakConcurrentRequests: providerPeak,
      };
  report.passed = report.scenarios.every((item) => item.passed);
}

main()
  .catch((error) => {
    report.errorCode = safeCode(error);
    process.stderr.write(`${report.errorCode}\n`);
  })
  .finally(async () => {
    try {
      await cleanup();
    } catch (error) {
      report.passed = false;
      report.cleanupErrorCode = safeCode(error);
    }
    report.finishedAt = new Date().toISOString();
    report.provider = real
      ? { endpoint, external: true, retriesPerRequest: 0, generationRequests: qaRequests }
      : {
          endpoint,
          external: false,
          requests: providerRequests,
          peakConcurrentRequests: providerPeak,
          generationRequests: qaRequests,
          ...(providerError ? { errorCode: providerError } : {}),
        };
    if (reportAllowed)
      fs.writeFileSync(reportPath, JSON.stringify(report, null, 2), { mode: 0o600 });
    await mongoose?.disconnect().catch(() => {});
    process.stdout.write(
      `PERFORMANCE ${report.passed && report.cleaned ? 'PASS' : 'FAIL'} ${runId}\n`,
    );
    process.exit(report.passed && report.cleaned ? 0 : 1);
  });
