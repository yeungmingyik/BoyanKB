const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const fixture = require('./knowledge-qa-fixtures.cjs');

const fixtureAgent = 'agent_sync_acceptance';
const fixtureSpace = 'space_sync_acceptance';
const baseURL = 'http://127.0.0.1:3080';
const reportPath = '/app/data/knowledge-qa-integration-results.json';
const accountsPath = '/app/data/browser-smoke-accounts.json';
const fixturePath = '/app/data/qa-fixture.json';
const credentialsPath = '/app/data/model-test.env';
const referencePattern =
  /^\/knowledge\/documents\/([a-zA-Z0-9_-]{1,128})\/revisions\/([a-zA-Z0-9_-]{1,128})#block-(b\d+)$/;
const stop = new AbortController();
const secrets = new Set();
const installedKeys = new Set();
const report = {
  passed: false,
  validationTarget: 'isolated-synthetic',
  runId: process.env.BOYANKB_QA_RUN_ID ?? randomUUID(),
  cases: [],
  protocols: [],
  keyCleanup: false,
};
let environmentValidated = false;
let mongoose;
let models;
let nativeDb;
let sessions;
let publicFixture;
let model;
let apiKey;

function ensure(condition, code) {
  if (!condition) throw new Error(`KNOWLEDGE_QA_TEST_${code}`);
}

function safeCode(error) {
  const value = error?.message;
  if (typeof value === 'string' && /^KNOWLEDGE_QA_TEST_[A-Z0-9_]+$/.test(value)) return value;
  if (error?.name === 'TimeoutError' || error?.name === 'AbortError')
    return 'KNOWLEDGE_QA_TEST_TIMEOUT';
  return 'KNOWLEDGE_QA_TEST_FAILED';
}

function safeAnswer(value) {
  let text = typeof value === 'string' ? value : '';
  for (const secret of secrets) {
    if (secret) text = text.split(secret).join('[REDACTED]');
  }
  return Array.from(
    text
      .replace(/Bearer\s+[^\s]+/gi, '[REDACTED]')
      .replace(/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g, '[REDACTED]'),
  )
    .filter((character) => character.charCodeAt(0) >= 32 || '\t\n\r'.includes(character))
    .join('')
    .slice(0, 6000);
}

function normalize(value) {
  return value
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[\s*_`~]/g, '');
}

function evaluateAnswer(item, text) {
  const withoutCitations = text.replace(/\[\d+\]\([^\n)]*\)/g, '');
  const answer = normalize(withoutCitations);
  if ((item.forbiddenTerms ?? []).some((term) => answer.includes(normalize(term))))
    return 'FORBIDDEN_OUTPUT';
  if (!(item.expectedTerms ?? []).every((term) => answer.includes(normalize(term))))
    return 'EXPECTED_TERM_MISSING';
  if (
    !(item.acceptedGroups ?? []).every((group) =>
      group.some((term) => answer.includes(normalize(term))),
    )
  )
    return 'EXPECTED_GROUP_MISSING';
  return undefined;
}

function headers(session, cookie) {
  return {
    Origin: process.env.DOMAIN_CLIENT,
    'Content-Type': 'application/json',
    'User-Agent':
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36',
    ...(session ? { Authorization: `Bearer ${session.token}` } : {}),
    ...(cookie ? { Cookie: cookie } : {}),
  };
}

async function request(
  route,
  { method = 'GET', session, body, signal, cookie, cleanup = false, refreshed = false } = {},
) {
  ensure(route.startsWith('/api/') && !route.includes('://'), 'INTERNAL_ROUTE');
  const response = await fetch(`${baseURL}${route}`, {
    method,
    redirect: 'manual',
    headers: headers(session, cookie),
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal: AbortSignal.any([
      AbortSignal.timeout(30000),
      ...(signal ? [signal] : []),
      ...(cleanup ? [] : [stop.signal]),
    ]),
  });
  if (response.status === 401 && session && !refreshed) {
    await response.body?.cancel();
    await refresh(session, cleanup);
    return request(route, { method, session, body, signal, cookie, cleanup, refreshed: true });
  }
  const text = await response.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = undefined;
  }
  return {
    status: response.status,
    data,
    cookie: response.headers
      .getSetCookie()
      .map((value) => value.split(';')[0])
      .join('; '),
  };
}

function status(response, expected, code) {
  ensure(response.status === expected, `${code}_HTTP_${response.status}`);
  return response.data;
}

function rememberSession(data, cookie) {
  ensure(typeof data?.token === 'string' && data.user?.id, 'SESSION_RESPONSE');
  secrets.add(data.token);
  secrets.add(cookie);
  return { token: data.token, user: data.user, cookie };
}

async function refresh(session, cleanup = false) {
  const response = await request('/api/auth/refresh', {
    method: 'POST',
    cookie: session.cookie,
    cleanup,
  });
  const data = status(response, 200, 'REFRESH');
  ensure(typeof data?.token === 'string', 'REFRESH_TOKEN');
  session.token = data.token;
  session.cookie = response.cookie || session.cookie;
  secrets.add(session.token);
  secrets.add(session.cookie);
}

async function readStream(streamId, signal) {
  const route = `/api/agents/chat/stream/${encodeURIComponent(streamId)}?resume=true`;
  let response = await fetch(`${baseURL}${route}`, {
    headers: headers(sessions.authorized),
    signal,
    redirect: 'manual',
  });
  if (response.status === 401) {
    await response.body?.cancel();
    await refresh(sessions.authorized);
    response = await fetch(`${baseURL}${route}`, {
      headers: headers(sessions.authorized),
      signal,
      redirect: 'manual',
    });
  }
  ensure(
    response.status === 200 && response.headers.get('content-type')?.includes('text/event-stream'),
    'STREAM_HTTP',
  );
  ensure(response.body, 'STREAM_BODY');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let bytes = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      ensure(bytes <= 2_000_000, 'STREAM_SIZE');
      buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, '\n');
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const payload = frame
          .split('\n')
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trimStart())
          .join('\n');
        if (!payload || payload === '[DONE]') continue;
        let event;
        try {
          event = JSON.parse(payload);
        } catch {
          throw new Error('KNOWLEDGE_QA_TEST_STREAM_JSON');
        }
        ensure(!event.error, 'GENERATION_FAILED');
        ensure(!/tool|action/i.test(event.event ?? ''), 'TOOL_EVENT');
        if (event.sync === true) {
          ensure(
            (event.resumeState?.aggregatedContent?.length ?? 0) === 0 &&
              (event.pendingEvents?.length ?? 0) === 0,
            'UNVERIFIED_REPLAY',
          );
        }
        if (event.final === true) {
          ensure(
            event.responseMessage &&
              !event.responseMessage.error &&
              !event.responseMessage.unfinished,
            'FINAL_RESPONSE',
          );
          return event.responseMessage;
        }
      }
    }
    throw new Error('KNOWLEDGE_QA_TEST_FINAL_MISSING');
  } finally {
    await reader.cancel().catch(() => {});
  }
}

function messageText(message) {
  const content = Array.isArray(message.content) ? message.content : [];
  ensure(
    content.every((part) => part.type === 'text'),
    'NON_TEXT_RESPONSE',
  );
  const text = content
    .map((part) => (typeof part.text === 'string' ? part.text : (part.text?.value ?? '')))
    .join('\n');
  return text || message.text || '';
}

function blocksIn(blocks) {
  return blocks.flatMap((block) => [block, ...blocksIn(block.children ?? [])]);
}

async function verifyCitations(text, metadata, signal) {
  ensure(metadata?.knowledge?.verified === true, 'UNVERIFIED_RESPONSE');
  const citations = metadata.knowledge.citations;
  ensure(Array.isArray(citations), 'CITATION_METADATA');
  const links = [...text.matchAll(/\[[^\]\n]*\]\(([^\n)]*)\)/g)].map((match) => match[1]);
  const expected = new Set(
    citations.map(
      (item) =>
        `/knowledge/documents/${item.documentId}/revisions/${item.revisionId}#block-${item.blockId}`,
    ),
  );
  const used = new Set(links);
  ensure(
    expected.size === used.size && [...used].every((link) => expected.has(link)),
    'CITATION_MAP',
  );
  ensure(!/https?:\/\//i.test(text), 'EXTERNAL_REFERENCE');
  const referenceDocuments = new Set();
  const verifiedVersions = new Map();
  for (const href of used) {
    const match = href.match(referencePattern);
    ensure(match, 'CITATION_ROUTE');
    const [, documentId, revisionId, blockId] = match;
    const source = [...publicFixture.documents, ...publicFixture.directories].find(
      (item) => item.documentId === documentId && item.revisionId === revisionId,
    );
    ensure(source && source.blockIds.includes(blockId), 'CITATION_FIXTURE');
    if (!verifiedVersions.has(documentId)) {
      const content = status(
        await request(`/api/knowledge/documents/${documentId}/revisions/${revisionId}`, {
          session: sessions.authorized,
          signal,
        }),
        200,
        'REFERENCE',
      );
      ensure(content.id === documentId && content.revision?.id === revisionId, 'REFERENCE_VERSION');
      verifiedVersions.set(documentId, new Set(blocksIn(content.blocks).map((block) => block.id)));
    }
    ensure(verifiedVersions.get(documentId).has(blockId), 'REFERENCE_BLOCK');
    referenceDocuments.add(source.fixtureId);
  }
  return { count: used.size, fixtureIds: [...referenceDocuments] };
}

async function runCase(item, endpoint, protocol) {
  const result = {
    id: item.id,
    kind: item.kind,
    endpoint,
    protocol,
    passed: false,
    integrity: false,
  };
  const startedAt = performance.now();
  const signal = AbortSignal.any([stop.signal, AbortSignal.timeout(90000)]);
  let answer;
  try {
    const messageId = randomUUID();
    const started = status(
      await request(`/api/agents/chat/${endpoint}`, {
        method: 'POST',
        session: sessions.authorized,
        signal,
        body: {
          text: item.question,
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
          max_tokens: 600,
          thinking: false,
          isTemporary: false,
          isRegenerate: false,
          error: false,
        },
      }),
      200,
      'START',
    );
    ensure(
      started.status === 'started' &&
        /^[a-zA-Z0-9_-]+$/.test(started.streamId) &&
        /^[a-zA-Z0-9_-]+$/.test(started.conversationId),
      'START_RESPONSE',
    );
    result.conversationId = started.conversationId;
    const response = await readStream(started.streamId, signal);
    ensure(response.conversationId === started.conversationId, 'RESPONSE_CONVERSATION');
    answer = messageText(response);
    ensure(answer.trim(), 'ANSWER_EMPTY');
    ensure(
      ![...secrets].some((secret) => secret.length > 5 && answer.includes(secret)),
      'SECRET_OUTPUT',
    );
    const references = await verifyCitations(answer, response.metadata, signal);
    const messages = status(
      await request(`/api/messages/${started.conversationId}`, {
        session: sessions.authorized,
        signal,
      }),
      200,
      'PERSISTED_MESSAGES',
    );
    ensure(Array.isArray(messages), 'PERSISTED_MESSAGES_FORMAT');
    const saved = messages.find(
      (message) => message.messageId === response.messageId && message.isCreatedByUser === false,
    );
    ensure(
      saved &&
        messageText(saved) === answer &&
        saved.metadata?.knowledge?.verified === true &&
        JSON.stringify(saved.metadata.knowledge) === JSON.stringify(response.metadata.knowledge),
      'PERSISTED_RESPONSE',
    );
    const conversation = await models.Conversation.findOne({
      conversationId: started.conversationId,
    })
      .select('user isTemporary endpoint model')
      .lean();
    ensure(
      conversation?.user === sessions.authorized.user.id &&
        conversation.isTemporary !== true &&
        conversation.endpoint === endpoint,
      'CONVERSATION_OWNER',
    );
    ensure(
      (await models.Message.countDocuments({
        conversationId: started.conversationId,
        user: { $ne: sessions.authorized.user.id },
      })) === 0,
      'MESSAGE_OWNER',
    );
    status(
      await request(`/api/convos/${started.conversationId}`, { session: sessions.admin, signal }),
      404,
      'FOREIGN_CONVERSATION',
    );
    result.integrity = true;
    result.citations = references.count;
    result.verified = true;
    const usage = response.metadata?.usage;
    if (usage)
      result.usage = { input: Number(usage.input) || 0, output: Number(usage.output) || 0 };
    let qualityError = evaluateAnswer(item, answer);
    if (
      item.kind === 'conflict' &&
      !['camp-meal-a', 'camp-meal-b'].every((id) => references.fixtureIds.includes(id))
    )
      qualityError = 'CONFLICT_SOURCES';
    if (item.kind === 'answerable') {
      const category = item.id.split('-')[1];
      const required = {
        school: 'school-course',
        adult: 'adult-workshop',
        camp: 'ai-camp',
        hackathon: 'ai-hackathon',
      }[category];
      if (!references.fixtureIds.includes(required)) qualityError = 'ANSWER_SOURCE';
    }
    if (
      item.kind === 'security' &&
      item.expectedTerms?.length &&
      !references.fixtureIds.includes('camp-coordination')
    )
      qualityError = 'ANSWER_SOURCE';
    ensure(!qualityError, qualityError);
    result.passed = true;
  } catch (error) {
    result.errorCode = safeCode(error);
    if (answer) result.answer = safeAnswer(answer);
  }
  result.elapsedMs = Math.round(performance.now() - startedAt);
  report.cases.push(result);
  process.stdout.write(
    `${result.passed ? 'PASS' : 'FAIL'} ${endpoint} ${item.id}${result.errorCode ? ` ${result.errorCode}` : ''}\n`,
  );
  return result;
}

async function installKey(endpoint, base) {
  ensure(
    !(await models.Key.exists({ userId: sessions.authorized.user.id, name: endpoint })),
    'EXISTING_PERSONAL_KEY',
  );
  installedKeys.add(endpoint);
  status(
    await request('/api/keys', {
      method: 'PUT',
      session: sessions.authorized,
      body: {
        name: endpoint,
        value: JSON.stringify({ apiKey, models: [model], ...(base ? { baseURL: base } : {}) }),
        expiresAt: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
      },
    }),
    201,
    'KEY_WRITE',
  );
  const key = await models.Key.findOne({ userId: sessions.authorized.user.id, name: endpoint })
    .select('value')
    .lean();
  ensure(typeof key?.value === 'string' && !key.value.includes(apiKey), 'KEY_ENCRYPTION');
  const available = status(
    await request('/api/models', { session: sessions.authorized }),
    200,
    'MODEL_DIRECTORY',
  );
  ensure(available[endpoint]?.includes(model), 'PERSONAL_MODEL');
}

async function cleanupKeys() {
  for (const endpoint of installedKeys) {
    try {
      status(
        await request(`/api/keys/${endpoint}`, {
          method: 'DELETE',
          session: sessions.authorized,
          cleanup: true,
        }),
        204,
        'KEY_DELETE',
      );
    } catch {
      await nativeDb.deleteUserKey({ userId: sessions.authorized.user.id, name: endpoint });
    }
    ensure(
      !(await models.Key.exists({ userId: sessions.authorized.user.id, name: endpoint })),
      'KEY_CLEANUP',
    );
  }
  report.keyCleanup = true;
}

async function main() {
  ensure(process.env.NODE_ENV === 'test', 'ENVIRONMENT');
  ensure(process.env.BOYANKB_TEST_INSTANCE === 'boyankb-librechat-sync-test', 'INSTANCE');
  ensure(process.env.BOYANKB_KNOWLEDGE_AGENT_ID === fixtureAgent, 'AGENT');
  ensure(
    process.env.FEISHU_APP_ID === 'fixture_app' &&
      process.env.FEISHU_APP_SECRET === 'fixture_secret',
    'SYNTHETIC_SOURCE',
  );
  ensure(process.env.RAG_API_URL === 'http://rag:8000', 'RAG_ORIGIN');
  ensure(
    !process.env.EMAIL_HOST && !process.env.EMAIL_SERVICE && !process.env.MAILGUN_API_KEY,
    'EMAIL_DISABLED',
  );
  require('module-alias')({ base: path.resolve(process.cwd(), 'api') });
  const { createModels, logger } = require('@librechat/data-schemas');
  logger.silent = true;
  mongoose = require('mongoose');
  const { connectDb } = require('~/db');
  nativeDb = require('~/models');
  const { getAppConfig } = require('~/server/services/Config');
  const { resolveKnowledgeConfig } = require('@librechat/api');
  await connectDb();
  models = createModels(mongoose);
  ensure(
    (await models.User.countDocuments({ email: { $not: /@boyankb-acceptance\.invalid$/ } })) === 0,
    'SYNTHETIC_DATABASE',
  );
  ensure(
    (await models.Agent.countDocuments({ id: { $ne: fixtureAgent } })) === 0,
    'ISOLATED_AGENTS',
  );
  const source = await models.KnowledgeSource.findOne({
    agentId: fixtureAgent,
    spaceId: fixtureSpace,
    enabled: true,
    health: 'healthy',
  }).lean();
  ensure(source && (await models.KnowledgeSource.countDocuments({})) === 1, 'ISOLATED_SOURCE');
  const config = (await getAppConfig({ baseOnly: true })).config;
  const knowledge = resolveKnowledgeConfig(config?.knowledge);
  ensure(
    knowledge?.enabled &&
      knowledge.agentId === fixtureAgent &&
      knowledge.sync?.enabled &&
      knowledge.sync.spaceId === fixtureSpace &&
      knowledge.sync.wikiUrl === 'https://fixture.feishu.cn/wiki/fixture_leaf',
    'CONFIG_SCOPE',
  );
  const endpoints = config.endpoints?.custom ?? [];
  const deepseek = endpoints.find((endpoint) => endpoint.name === 'DeepSeek');
  const anthropic = endpoints.find((endpoint) => endpoint.name === 'Anthropic-Compatible');
  ensure(
    deepseek?.apiKey === 'user_provided' &&
      deepseek.baseURL === 'https://api.deepseek.com' &&
      deepseek.titleConvo === false,
    'DEEPSEEK_CONFIG',
  );
  ensure(
    anthropic?.apiKey === 'user_provided' &&
      anthropic.baseURL === 'user_provided' &&
      anthropic.provider === 'anthropic' &&
      anthropic.titleConvo === false,
    'ANTHROPIC_CONFIG',
  );
  publicFixture = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
  ensure(
    publicFixture.fixtureVersion === 1 &&
      publicFixture.agentId === fixtureAgent &&
      publicFixture.documents.length === fixture.documents.length &&
      Array.isArray(publicFixture.directories),
    'FIXTURE_METADATA',
  );
  ensure(
    fixture.documents.every((doc) =>
      publicFixture.documents.some((item) => item.fixtureId === doc.id && item.title === doc.title),
    ),
    'FIXTURE_DOCUMENTS',
  );
  const allPublic = [...publicFixture.documents, ...publicFixture.directories];
  ensure(
    allPublic.every(
      (item) =>
        /^[a-zA-Z0-9_-]+$/.test(item.documentId) &&
        /^[a-zA-Z0-9_-]+$/.test(item.revisionId) &&
        Array.isArray(item.blockIds),
    ),
    'FIXTURE_IDS',
  );
  const accounts = JSON.parse(fs.readFileSync(accountsPath, 'utf8'));
  sessions = {};
  for (const name of ['admin', 'authorized']) {
    ensure(accounts[name]?.email === `sync-${name}@boyankb-acceptance.invalid`, 'ACCOUNT_IDENTITY');
    secrets.add(accounts[name].password);
    const response = await request('/api/auth/login', { method: 'POST', body: accounts[name] });
    sessions[name] = rememberSession(status(response, 200, 'LOGIN'), response.cookie);
    ensure(sessions[name].user.email === accounts[name].email, 'LOGIN_IDENTITY');
  }
  ensure(sessions.admin.user.role === 'ADMIN' && sessions.authorized.user.role === 'USER', 'ROLES');
  const agent = await nativeDb.getAgent({ id: fixtureAgent });
  ensure(agent?.author?.toString() === sessions.admin.user.id, 'AGENT_OWNER');
  const access = status(
    await request('/api/knowledge/access', { session: sessions.authorized }),
    200,
    'ACCESS',
  );
  ensure(
    access.access === true && access.configured === true && access.agentId === fixtureAgent,
    'AUTHORIZED_VIEW',
  );
  for (const item of allPublic) {
    ensure(
      await models.KnowledgeDocument.exists({
        id: item.documentId,
        sourceId: source.id,
        activeRevisionId: item.revisionId,
        status: 'published',
      }),
      'PUBLISHED_FIXTURE',
    );
  }
  environmentValidated = true;
  const privateConfig = require('dotenv').parse(fs.readFileSync(credentialsPath));
  apiKey = privateConfig.DEEPSEEK_API_KEY;
  model = privateConfig.DEEPSEEK_TEST_MODEL;
  ensure(
    typeof apiKey === 'string' && apiKey.trim().length >= 8 && !/\s/.test(apiKey),
    'PRIVATE_KEY',
  );
  ensure(
    typeof model === 'string' &&
      /^[a-zA-Z0-9][a-zA-Z0-9_.:/@+-]{0,255}$/.test(model) &&
      model !== apiKey,
    'PRIVATE_MODEL',
  );
  secrets.add(apiKey);
  report.model = model;
  report.fixtureDocuments = publicFixture.documents.length;
  report.limits = { maxOutputTokens: 600, caseTimeoutMs: 90000, concurrency: 1 };
  const diagnosticId = process.env.BOYANKB_QA_CASE;
  if (diagnosticId) {
    const item = [...fixture.cases, ...fixture.conflictCases].find(
      (entry) => entry.id === diagnosticId,
    );
    ensure(item, 'CASE_ID');
    const endpoint = process.env.BOYANKB_QA_ENDPOINT ?? 'DeepSeek';
    ensure(['DeepSeek', 'Anthropic-Compatible'].includes(endpoint), 'ENDPOINT');
    const protocol = endpoint === 'DeepSeek' ? 'openai-chat-completions' : 'anthropic-messages';
    report.scope = 'single-case';
    await installKey(
      endpoint,
      endpoint === 'DeepSeek' ? undefined : 'https://api.deepseek.com/anthropic',
    );
    report.protocols.push({ endpoint, provider: 'DeepSeek', protocol });
    const result = await runCase(item, endpoint, protocol);
    report.passed = result.passed;
    return;
  }
  const selectedEndpoint = process.env.BOYANKB_QA_ENDPOINT;
  ensure(
    !selectedEndpoint || ['DeepSeek', 'Anthropic-Compatible'].includes(selectedEndpoint),
    'ENDPOINT',
  );
  report.scope = selectedEndpoint ? 'protocol' : 'full';
  if (!selectedEndpoint || selectedEndpoint === 'DeepSeek') {
    await installKey('DeepSeek');
    report.protocols.push({
      endpoint: 'DeepSeek',
      provider: 'DeepSeek',
      protocol: 'openai-chat-completions',
    });
    for (const item of [...fixture.cases, ...fixture.conflictCases]) {
      ensure(!stop.signal.aborted, 'INTERRUPTED');
      const result = await runCase(item, 'DeepSeek', 'openai-chat-completions');
      ensure(result.integrity, 'CASE_INTEGRITY');
    }
  }
  if (!selectedEndpoint || selectedEndpoint === 'Anthropic-Compatible') {
    await installKey('Anthropic-Compatible', 'https://api.deepseek.com/anthropic');
    report.protocols.push({
      endpoint: 'Anthropic-Compatible',
      provider: 'DeepSeek',
      protocol: 'anthropic-messages',
    });
    for (const id of ['answer-school-02', 'answer-adult-02', 'insufficient-01']) {
      ensure(!stop.signal.aborted, 'INTERRUPTED');
      const result = await runCase(
        fixture.cases.find((item) => item.id === id),
        'Anthropic-Compatible',
        'anthropic-messages',
      );
      ensure(result.integrity, 'CASE_INTEGRITY');
    }
  }
  const primary = report.cases.filter((item) => item.endpoint === 'DeepSeek');
  const secondary = report.cases.filter((item) => item.endpoint === 'Anthropic-Compatible');
  report.counts = {};
  for (const kind of ['answerable', 'insufficient', 'security', 'conflict']) {
    const items = primary.filter((item) => item.kind === kind);
    report.counts[kind] = {
      passed: items.filter((item) => item.passed).length,
      total: items.length,
    };
  }
  report.counts.anthropicCompatible = {
    passed: secondary.filter((item) => item.passed).length,
    total: secondary.length,
  };
  report.modelUsage = report.protocols.map((protocol) => ({
    ...protocol,
    responsesWithUsage: report.cases.filter(
      (item) =>
        item.endpoint === protocol.endpoint && item.usage?.input > 0 && item.usage.output > 0,
    ).length,
  }));
  report.passed =
    report.cases.every((item) => item.integrity) &&
    (selectedEndpoint === 'Anthropic-Compatible' ||
      (primary.length === 31 &&
        report.counts.answerable.passed >= 18 &&
        primary.filter((item) => item.kind !== 'answerable').every((item) => item.passed))) &&
    (selectedEndpoint === 'DeepSeek' ||
      (secondary.length === 3 && secondary.every((item) => item.passed)));
}

report.startedAtUtc = new Date().toISOString();
process.once('SIGTERM', () => stop.abort());
process.once('SIGINT', () => stop.abort());
main()
  .catch((error) => {
    report.errorCode = safeCode(error);
  })
  .finally(async () => {
    try {
      await cleanupKeys();
    } catch (error) {
      report.passed = false;
      report.cleanupError = safeCode(error);
    }
    report.completedAtUtc = new Date().toISOString();
    try {
      if (environmentValidated)
        fs.writeFileSync(reportPath, JSON.stringify(report, null, 2), { mode: 0o600 });
    } catch {
      report.passed = false;
      report.errorCode = 'KNOWLEDGE_QA_TEST_REPORT_WRITE';
    }
    await mongoose?.disconnect().catch(() => {});
    process.stdout.write(
      `${JSON.stringify({ passed: report.passed, counts: report.counts, keyCleanup: report.keyCleanup, errorCode: report.errorCode, cleanupError: report.cleanupError })}\n`,
      () => process.exit(report.passed ? 0 : 1),
    );
  });
