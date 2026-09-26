const fs = require('node:fs');
const path = require('node:path');
const { createServer } = require('node:http');
const { randomBytes, randomUUID } = require('node:crypto');
const { performance } = require('node:perf_hooks');
const { setTimeout: delay } = require('node:timers/promises');

const fixtureAgent = 'agent_sync_acceptance';
const fixtureSpace = 'space_sync_acceptance';
const fixtureProvider = 'BoyanFixture';
const fixtureModel = 'boyan-slow-test';
const fixtureOrigin = 'http://127.0.0.1:19091/v1';
const question = '星帆机器人课程每班最多多少名学生？';
const reportPath = '/app/data/knowledge-stream-integration-results.json';
const report = { passed: false, checks: [], measurements: [], cleaned: false };
const nonce = randomUUID().replaceAll('-', '');
const selectedCase =
  process.argv.find((argument) => argument.startsWith('--case='))?.slice(7) ?? 'all';
const ownedUsers = [];
const ownedTurns = [];
const attachments = [];
const providerStates = [];
let mongoose;
let models;
let runAsSystem;
let provider;
let currentProviderState;
let admin;
let permissionPath;
let banStore;
let restoreSource;
let validated = false;
let reportAllowed = false;

function ensure(condition, code) {
  if (!condition) throw new Error(`KNOWLEDGE_STREAM_TEST_${code}`);
}

function safeCode(error) {
  const code = error?.code ?? error?.message;
  return typeof code === 'string' && /^KNOWLEDGE_STREAM_TEST_[A-Z0-9_]+$/.test(code)
    ? code
    : 'KNOWLEDGE_STREAM_TEST_FAILED';
}

function pass(name) {
  report.checks.push(name);
  process.stdout.write(`PASS ${name}\n`);
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
  const response = await fetch(`http://127.0.0.1:3080${route}`, {
    method,
    redirect: 'manual',
    headers: headers(session),
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal: AbortSignal.timeout(45000),
  });
  const text = await response.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = undefined;
  }
  return { status: response.status, data, text };
}

function status(response, expected, code) {
  ensure(response.status === expected, `${code}_HTTP_${response.status}`);
  return response;
}

async function until(predicate, timeoutMs, code) {
  const started = performance.now();
  while (!(await predicate())) {
    ensure(performance.now() - started < timeoutMs, code);
    await delay(25);
  }
}

async function attach(turn, resume = false) {
  const abortController = new AbortController();
  const response = await fetch(
    `http://127.0.0.1:3080/api/agents/chat/stream/${turn.streamId}?generationCreatedAt=${turn.generationCreatedAt}&resume=${resume}`,
    {
      headers: headers(turn.session),
      redirect: 'manual',
      signal: AbortSignal.any([
        abortController.signal,
        AbortSignal.timeout(turn.providerState.failure ? 180000 : 90000),
      ]),
    },
  );
  ensure(response.status === 200, `ATTACH_HTTP_${response.status}`);
  ensure(
    response.headers.get('content-type')?.includes('text/event-stream'),
    'ATTACH_CONTENT_TYPE',
  );
  const state = {
    abortController,
    text: '',
    frames: [],
    closed: false,
    closedAt: 0,
    failed: false,
  };
  attachments.push(state);
  state.finished = (async () => {
    const decoder = new TextDecoder();
    let pending = '';
    try {
      for await (const bytes of response.body) {
        const chunk = decoder.decode(bytes, { stream: true });
        state.text += chunk;
        ensure(state.text.length <= 1024 * 1024, 'STREAM_SIZE_LIMIT');
        pending += chunk.replaceAll('\r\n', '\n');
        let boundary;
        while ((boundary = pending.indexOf('\n\n')) >= 0) {
          const frame = pending.slice(0, boundary);
          pending = pending.slice(boundary + 2);
          const payload = frame
            .split('\n')
            .filter((line) => line.startsWith('data: '))
            .map((line) => line.slice(6))
            .join('\n');
          if (payload) state.frames.push(JSON.parse(payload));
        }
      }
    } catch {
      state.failed = !abortController.signal.aborted;
    } finally {
      state.closed = true;
      state.closedAt = performance.now();
    }
  })();
  return state;
}

function assertHidden(value, marker, code) {
  const serialized = typeof value === 'string' ? value : JSON.stringify(value);
  ensure(!serialized.includes(marker), `${code}_RAW_BODY`);
}

function assertEmptyBuffers(response) {
  ensure(
    Array.isArray(response.aggregatedContent) && response.aggregatedContent.length === 0,
    'STATUS_CONTENT',
  );
  ensure((response.resumeState?.runSteps ?? []).length === 0, 'STATUS_STEPS');
  ensure((response.resumeState?.aggregatedContent ?? []).length === 0, 'STATUS_RESUME_CONTENT');
  ensure((response.pendingEvents ?? []).length === 0 && !response.pendingAction, 'STATUS_EVENTS');
}

async function grant(session, allow) {
  const { AccessRoleIds } = require('librechat-data-provider');
  return status(
    await request(permissionPath, {
      method: 'PUT',
      session: admin,
      body: {
        updated: allow
          ? [{ type: 'user', id: session.user.id, accessRoleId: AccessRoleIds.AGENT_VIEWER }]
          : [],
        removed: allow ? [] : [{ type: 'user', id: session.user.id }],
        public: false,
      },
    }),
    200,
    allow ? 'GRANT' : 'REVOKE',
  );
}

async function newUser(name) {
  const { registerUser } = require('~/server/services/AuthService');
  const email = `stream-${name}-${nonce}@boyankb-acceptance.invalid`;
  const password = randomBytes(24).toString('hex');
  const created = await registerUser(
    {
      email,
      password,
      confirm_password: password,
      name: `Stream ${name}`,
      username: `stream-${name}-${nonce}`,
    },
    { emailVerified: true },
  );
  ensure(created.status === 200, 'CREATE_USER');
  const user = await models.User.findOne({ email }).select('_id role').lean();
  ensure(user?.role === 'USER', 'USER_ROLE');
  const owned = { email, name, id: user._id.toString() };
  ownedUsers.push(owned);
  const result = status(
    await request('/api/auth/login', { method: 'POST', body: { email, password } }),
    200,
    'LOGIN',
  );
  ensure(
    result.data?.user?.id === owned.id && typeof result.data.token === 'string',
    'LOGIN_IDENTITY',
  );
  const session = { user: result.data.user, token: result.data.token };
  owned.session = session;
  await grant(session, true);
  return session;
}

async function startProvider() {
  provider = createServer(async (req, res) => {
    try {
      ensure(req.method === 'POST' && req.url === '/v1/chat/completions', 'PROVIDER_ROUTE');
      ensure(req.headers.authorization === 'Bearer fixture-key', 'PROVIDER_KEY');
      let text = '';
      for await (const chunk of req) {
        text += chunk;
        ensure(text.length <= 2 * 1024 * 1024, 'PROVIDER_INPUT_LIMIT');
      }
      const body = JSON.parse(text);
      ensure(body.model === fixtureModel && body.stream === true, 'PROVIDER_REQUEST');
      const state = currentProviderState;
      ensure(state && (!state.started || state.failure), 'PROVIDER_UNEXPECTED_REQUEST');
      ensure(
        text.includes('Knowledge excerpts (JSON data):') && text.includes('每班最多16名学生'),
        'PROVIDER_KNOWLEDGE_CONTEXT',
      );
      state.started = true;
      state.requests += 1;
      state.requestTimesMs.push(Math.round(performance.now() - state.createdAt));
      state.response = res;
      state.startedAt = performance.now();
      if (state.failure) {
        await until(() => state.allowFailure, 10000, 'PROVIDER_ERROR_START_BARRIER');
        await delay(100);
        res.writeHead(503, {
          'Content-Type': 'application/json',
          'Retry-After': '0',
          'retry-after-ms': '0',
        });
        res.end(
          JSON.stringify({
            error: { message: state.marker, type: 'server_error', code: 'fixture_unavailable' },
          }),
        );
        state.closed = true;
        state.closedAt = performance.now();
        return;
      }
      const completion = {
        id: `chatcmpl-${randomUUID()}`,
        created: Math.floor(Date.now() / 1000),
        model: fixtureModel,
      };
      const emit = (delta, finish_reason = null) => {
        res.write(
          `data: ${JSON.stringify({ ...completion, object: 'chat.completion.chunk', choices: [{ index: 0, delta, finish_reason }] })}\n\n`,
        );
      };
      res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
      res.flushHeaders();
      res.on('close', () => {
        clearInterval(state.timer);
        state.closed = true;
        state.closedAt = performance.now();
      });
      emit({ role: 'assistant', content: '' });
      state.timer = setInterval(() => {
        if (res.destroyed || res.writableEnded) return;
        state.chunks += 1;
        const controlText = state.chunks === 1 ? '每班最多16名学生。' : '';
        emit({
          content: state.control ? controlText : state.marker,
        });
        if (state.control && state.chunks >= 32) {
          emit({ content: '[1]' });
          emit({}, 'stop');
          state.completed = true;
          res.end('data: [DONE]\n\n');
        }
      }, 250);
    } catch (error) {
      if (currentProviderState) currentProviderState.errorCode = safeCode(error);
      if (!res.headersSent) res.writeHead(400);
      res.end();
    }
  });
  await new Promise((resolve, reject) => {
    provider.once('error', reject);
    provider.listen(19091, '127.0.0.1', resolve);
  });
}

async function startTurn(name, control = false, options = {}) {
  const session = options.session ?? (await newUser(name));
  const providerState = {
    control,
    failure: options.failure === true,
    marker: `BOYANKB_UNVERIFIED_${nonce}_${name}`,
    chunks: 0,
    requests: 0,
    createdAt: performance.now(),
    requestTimesMs: [],
    started: false,
    closed: false,
    completed: false,
  };
  providerStates.push(providerState);
  currentProviderState = providerState;
  const messageId = randomUUID();
  const started = status(
    await request(`/api/agents/chat/${fixtureProvider}`, {
      method: 'POST',
      session,
      body: {
        text: question,
        sender: 'User',
        clientTimestamp: new Date().toISOString(),
        isCreatedByUser: true,
        parentMessageId: options.parentMessageId ?? '00000000-0000-0000-0000-000000000000',
        conversationId: options.conversationId ?? 'new',
        messageId,
        ...(options.failure ? {} : { responseMessageId: `${messageId}_response` }),
        endpoint: fixtureProvider,
        endpointType: 'custom',
        model: fixtureModel,
        isTemporary: false,
        isRegenerate: false,
        error: false,
      },
    }),
    200,
    'START',
  );
  ensure(
    started.data?.status === 'started' && /^[a-f0-9-]{36}$/.test(started.data.streamId),
    'START_IDENTITY',
  );
  const turn = { ...started.data, session, providerState };
  ownedTurns.push(turn);
  turn.original = await attach(turn);
  await until(
    () =>
      providerState.errorCode ||
      (providerState.failure ? providerState.started : providerState.chunks >= 2),
    45000,
    'PROVIDER_START_TIMEOUT',
  );
  ensure(
    !providerState.errorCode,
    providerState.errorCode?.replace('KNOWLEDGE_STREAM_TEST_', '') ?? 'PROVIDER_FAILED',
  );
  ensure(!providerState.closed, 'PROVIDER_EARLY_CLOSE');
  turn.resumed = await attach(turn, true);
  const state = status(
    await request(`/api/agents/chat/status/${turn.streamId}`, { session }),
    200,
    'ACTIVE_STATUS',
  );
  ensure(state.data.active === true, 'ACTIVE_TURN');
  assertEmptyBuffers(state.data);
  assertHidden(state.text, providerState.marker, 'ACTIVE_STATUS');
  for (const stream of [turn.original, turn.resumed])
    assertHidden(stream.text, providerState.marker, 'ACTIVE_STREAM');
  providerState.allowFailure = true;
  return turn;
}

async function assertVerifiedTurn(turn) {
  await until(() => turn.original.closed && turn.resumed.closed, 15000, 'CONTROL_TIMEOUT');
  ensure(turn.providerState.completed, 'CONTROL_MODEL_COMPLETED');
  for (const stream of [turn.original, turn.resumed]) {
    ensure(!stream.failed, 'CONTROL_STREAM_FAILED');
    const finals = stream.frames.filter(
      (frame) => frame.final && frame.responseMessage?.metadata?.knowledge?.verified === true,
    );
    ensure(finals.length === 1, 'CONTROL_VERIFIED_FINAL');
    const answer = JSON.stringify(finals[0].responseMessage);
    ensure(
      answer.includes('16名学生') && answer.includes('/knowledge/documents/'),
      'CONTROL_CITATION',
    );
    ensure(
      !stream.frames
        .filter((frame) => !frame.final)
        .some((frame) => JSON.stringify(frame).includes('每班最多16名学生')),
      'CONTROL_EARLY_BODY',
    );
  }
  await assertPersisted(turn, true);
}

async function modelFailureAndRetry() {
  const failed = await startTurn('failure', false, { failure: true });
  await until(
    () => failed.original.closed && failed.resumed.closed,
    150000,
    'MODEL_FAILURE_TIMEOUT',
  );
  ensure(
    failed.providerState.requests >= 1 && !failed.providerState.completed,
    'MODEL_FAILURE_OBSERVED',
  );
  report.observations = [
    {
      scenario: 'MODEL_SERVICE_FAILURE',
      providerRequests: failed.providerState.requests,
      requestTimesMs: failed.providerState.requestTimesMs,
      streams: [failed.original, failed.resumed].map((stream) => ({
        frameCount: stream.frames.length,
        errorFrames: stream.frames.filter((frame) => frame.error || frame.responseMessage?.error)
          .length,
        verifiedFinals: stream.frames.filter(
          (frame) => frame.responseMessage?.metadata?.knowledge?.verified === true,
        ).length,
        containsSyntheticErrorBody: stream.text.includes(failed.providerState.marker),
      })),
    },
  ];
  for (const stream of [failed.original, failed.resumed]) {
    assertHidden(stream.text, failed.providerState.marker, 'MODEL_FAILURE_STREAM');
    ensure(!stream.failed, 'MODEL_FAILURE_TRANSPORT');
    ensure(
      !stream.frames.some((frame) => frame.responseMessage?.metadata?.knowledge?.verified === true),
      'MODEL_FAILURE_FALSE_ANSWER',
    );
    ensure(
      stream.frames.some((frame) => frame.error || frame.responseMessage?.error),
      'MODEL_FAILURE_VISIBLE_ERROR',
    );
  }
  const current = status(
    await request(`/api/agents/chat/status/${failed.streamId}`, { session: failed.session }),
    200,
    'MODEL_FAILURE_STATUS',
  );
  ensure(current.data?.active === false, 'MODEL_FAILURE_SETTLED');
  assertHidden(current.text, failed.providerState.marker, 'MODEL_FAILURE_STATUS');
  ensure(
    (current.data.aggregatedContent ?? []).length === 0 &&
      (current.data.resumeState?.aggregatedContent ?? []).length === 0,
    'MODEL_FAILURE_STATUS_CONTENT',
  );
  const reconnect = await request(
    `/api/agents/chat/stream/${failed.streamId}?resume=true&generationCreatedAt=${failed.generationCreatedAt}`,
    { session: failed.session },
  );
  ensure([200, 404].includes(reconnect.status), 'MODEL_FAILURE_RECONNECT');
  assertHidden(reconnect.text, failed.providerState.marker, 'MODEL_FAILURE_RECONNECT');
  ensure(!reconnect.text.includes('"verified":true'), 'MODEL_FAILURE_RECONNECT_ANSWER');
  await assertPersisted(failed, false);
  const lastMessage = await models.Message.findOne({
    user: failed.session.user.id,
    conversationId: failed.conversationId,
  })
    .sort({ createdAt: -1, _id: -1 })
    .select('messageId')
    .lean();
  ensure(typeof lastMessage?.messageId === 'string', 'MODEL_FAILURE_RETRY_PARENT');
  const recovered = await startTurn('retry', true, {
    session: failed.session,
    conversationId: failed.conversationId,
    parentMessageId: lastMessage.messageId,
  });
  ensure(recovered.conversationId === failed.conversationId, 'MODEL_RETRY_CONVERSATION');
  await assertVerifiedTurn(recovered);
  const stored = await models.Message.find({
    user: failed.session.user.id,
    conversationId: failed.conversationId,
  }).lean();
  assertHidden(stored, failed.providerState.marker, 'MODEL_RETRY_PERSISTENCE');
  report.measurements.push({
    scenario: 'MODEL_SERVICE_FAILURE_AND_RETRY',
    failedProviderRequests: failed.providerState.requests,
    failedRequestTimesMs: failed.providerState.requestTimesMs,
    successfulRetryRequests: recovered.providerState.requests,
  });
  pass('MODEL_SERVICE_FAILURE_AND_RETRY');
}

async function assertPersisted(turn, verified) {
  const messages = await models.Message.find({
    user: turn.session.user.id,
    conversationId: turn.conversationId,
  }).lean();
  assertHidden(messages, turn.providerState.marker, 'PERSISTED');
  const replies = messages.filter((message) => !message.isCreatedByUser);
  if (verified) {
    ensure(
      replies.some((message) => message.metadata?.knowledge?.verified === true),
      'PERSISTED_VERIFIED',
    );
  } else {
    ensure(
      replies.every((message) => !message.text && (message.content ?? []).length === 0),
      'PERSISTED_UNVERIFIED_CONTENT',
    );
  }
}

async function canceled(turn, name, mutate, deniedStatus) {
  const changedAt = performance.now();
  await mutate();
  await until(
    () => turn.providerState.closed && turn.original.closed && turn.resumed.closed,
    5500,
    `${name}_STOP_TIMEOUT`,
  );
  const providerMs = Math.round(turn.providerState.closedAt - changedAt);
  const subscriberMs = Math.round(
    Math.max(turn.original.closedAt, turn.resumed.closedAt) - changedAt,
  );
  ensure(providerMs >= 0 && providerMs <= 5000, `${name}_PROVIDER_DEADLINE`);
  ensure(subscriberMs >= 0 && subscriberMs <= 5000, `${name}_STREAM_DEADLINE`);
  ensure(!turn.providerState.completed, `${name}_PROVIDER_NOT_ABORTED`);
  for (const stream of [turn.original, turn.resumed]) {
    assertHidden(stream.text, turn.providerState.marker, `${name}_STREAM`);
    ensure(
      !stream.frames.some((frame) => frame.responseMessage?.metadata?.knowledge?.verified === true),
      `${name}_VERIFIED_AFTER_REVOKE`,
    );
  }
  for (const route of [
    `/api/agents/chat/status/${turn.streamId}`,
    `/api/agents/chat/stream/${turn.streamId}?resume=true&generationCreatedAt=${turn.generationCreatedAt}`,
  ]) {
    const response = await request(route, { session: turn.session });
    ensure(deniedStatus.includes(response.status), `${name}_RECONNECT_HTTP_${response.status}`);
    if (response.status === 200) {
      ensure(
        response.data?.active === false && !response.data?.responseMessage,
        `${name}_INACTIVE_STATUS`,
      );
      ensure((response.data.aggregatedContent ?? []).length === 0, `${name}_INACTIVE_CONTENT`);
    }
    assertHidden(response.text, turn.providerState.marker, `${name}_RECONNECT`);
  }
  await delay(500);
  await assertPersisted(turn, false);
  report.measurements.push({
    scenario: name,
    providerAbortMs: providerMs,
    subscriberCloseMs: subscriberMs,
    providerChunksBeforeAbort: turn.providerState.chunks,
  });
  pass(name);
}

async function cleanup() {
  let restorationFailed = false;
  if (restoreSource) {
    await restoreSource().catch(() => {
      restorationFailed = true;
    });
  }
  for (const state of providerStates) {
    clearInterval(state.timer);
    state.response?.destroy();
  }
  for (const attachment of attachments) attachment.abortController.abort();
  await Promise.allSettled(attachments.map((attachment) => attachment.finished));
  if (provider?.listening) {
    provider.closeAllConnections();
    await new Promise((resolve) => provider.close(resolve));
  }
  if (validated && ownedUsers.length) {
    for (const turn of ownedTurns) {
      if (!turn.providerState.closed) {
        await request('/api/agents/chat/abort', {
          session: turn.session,
          method: 'POST',
          body: { streamId: turn.streamId, generationCreatedAt: turn.generationCreatedAt },
        }).catch(() => {});
      }
    }
    await delay(1500);
    for (const owned of ownedUsers) {
      ensure(
        owned.email === `stream-${owned.name}-${nonce}@boyankb-acceptance.invalid`,
        'CLEANUP_IDENTITY',
      );
      await banStore.delete(owned.id);
      await runAsSystem(async () => {
        for (const name of ['Session', 'Message', 'Conversation', 'Transaction', 'Balance']) {
          if (models[name]) await models[name].deleteMany({ user: owned.id });
        }
        await models.Key.deleteMany({ userId: owned.id });
        await models.AclEntry.deleteMany({ principalType: 'user', principalId: owned.id });
        await models.User.deleteOne({ _id: owned.id, email: owned.email });
      });
    }
    await delay(500);
    const ids = ownedUsers.map((owned) => owned.id);
    const remaining = await Promise.all([
      models.User.countDocuments({ _id: { $in: ids } }),
      models.Message.countDocuments({ user: { $in: ids } }),
      models.Session.countDocuments({ user: { $in: ids } }),
      models.Conversation.countDocuments({ user: { $in: ids } }),
      models.Transaction.countDocuments({ user: { $in: ids } }),
      models.Balance.countDocuments({ user: { $in: ids } }),
      models.Key.countDocuments({ userId: { $in: ids } }),
      models.AclEntry.countDocuments({ principalType: 'user', principalId: { $in: ids } }),
    ]);
    ensure(
      remaining.every((count) => count === 0),
      'CLEANUP_ROWS',
    );
    report.cleanedUsers = ownedUsers.length;
  }
  ensure(!restorationFailed, 'SOURCE_RESTORE');
  report.cleaned = true;
}

async function main() {
  ensure(
    process.argv
      .slice(2)
      .every(
        (argument) =>
          argument === '--source-changes' || /^--case=(all|model-failure|source)$/.test(argument),
      ) &&
      ['all', 'model-failure', 'source'].includes(selectedCase) &&
      (selectedCase !== 'source' || process.argv.includes('--source-changes')) &&
      (selectedCase !== 'model-failure' || !process.argv.includes('--source-changes')),
    'ARGUMENTS',
  );
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
  reportAllowed = true;
  require('module-alias')({ base: path.resolve(process.cwd(), 'api') });
  mongoose = require('mongoose');
  const schemas = require('@librechat/data-schemas');
  schemas.logger.silent = true;
  runAsSystem = schemas.runAsSystem;
  const { connectDb } = require('~/db');
  const { getAppConfig } = require('~/server/services/Config');
  const { resolveKnowledgeConfig } = require('@librechat/api');
  const { ViolationTypes } = require('librechat-data-provider');
  await connectDb();
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
  const config = await getAppConfig({ baseOnly: true });
  const knowledge = resolveKnowledgeConfig(config.config?.knowledge);
  ensure(
    knowledge?.enabled &&
      knowledge.sync?.enabled &&
      knowledge.agentId === fixtureAgent &&
      knowledge.sync.spaceId === fixtureSpace &&
      knowledge.sync.wikiUrl === 'https://fixture.feishu.cn/wiki/fixture_leaf',
    'CONFIG_SCOPE',
  );
  const endpoint = config.config?.endpoints?.custom?.find(
    (entry) => entry.name === fixtureProvider,
  );
  ensure(
    endpoint?.baseURL === fixtureOrigin &&
      endpoint.apiKey === 'fixture-key' &&
      endpoint.models?.default?.includes(fixtureModel) &&
      endpoint.models.fetch === false &&
      endpoint.titleConvo === false,
    'FIXTURE_PROVIDER_CONFIG',
  );
  const accounts = JSON.parse(fs.readFileSync('/app/data/browser-smoke-accounts.json', 'utf8'));
  ensure(accounts.admin?.email === 'sync-admin@boyankb-acceptance.invalid', 'ADMIN_IDENTITY');
  const login = status(
    await request('/api/auth/login', { method: 'POST', body: accounts.admin }),
    200,
    'ADMIN_LOGIN',
  );
  ensure(login.data?.user?.role === 'ADMIN' && typeof login.data.token === 'string', 'ADMIN_ROLE');
  admin = { token: login.data.token, user: login.data.user };
  const agent = await models.Agent.findOne({ id: fixtureAgent }).select('_id author').lean();
  ensure(agent?.author?.toString() === admin.user.id, 'AGENT_OWNER');
  permissionPath = `/api/permissions/agent/${agent._id.toString()}`;
  const source = await runAsSystem(() =>
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
  banStore = require('~/cache/getLogStores')(ViolationTypes.BAN);
  validated = true;
  await startProvider();

  if (selectedCase === 'all') {
    const control = await startTurn('control', true);
    await assertVerifiedTurn(control);
    pass('native verified final, reconnect and durable answer control');
  }

  if (selectedCase !== 'source') await modelFailureAndRetry();

  if (selectedCase === 'all') {
    const revoked = await startTurn('revoke');
    await canceled(revoked, 'VIEW_REVOKED', () => grant(revoked.session, false), [401, 403]);

    const banned = await startTurn('ban');
    await canceled(
      banned,
      'USER_BANNED',
      async () => {
        const banViolation = require('~/cache/banViolation');
        await banViolation(
          {},
          { clearCookie: () => {} },
          {
            type: ViolationTypes.CONCURRENT,
            user_id: banned.session.user.id,
            prev_count: 0,
            violation_count: 1000000,
            duration: 60000,
          },
        );
        ensure(Boolean(await banStore.get(banned.session.user.id)), 'BAN_PERSISTED');
        ensure(
          (await models.Session.countDocuments({ user: banned.session.user.id })) === 0,
          'BAN_SESSIONS_REVOKED',
        );
      },
      [401, 403],
    );
  }

  if (process.argv.includes('--source-changes')) {
    const paused = await startTurn('source');
    const restorePausedSource = async () => {
      const restored = await runAsSystem(() =>
        models.KnowledgeSource.updateOne(
          {
            id: source.id,
            agentId: fixtureAgent,
            spaceId: fixtureSpace,
            health: 'paused',
            accessEpoch: source.accessEpoch,
          },
          { $set: { health: 'healthy' } },
        ),
      );
      ensure(restored.matchedCount === 1, 'SOURCE_RESTORE');
      restoreSource = undefined;
    };
    await canceled(
      paused,
      'SOURCE_PAUSED',
      async () => {
        const changed = await runAsSystem(() =>
          models.KnowledgeSource.updateOne(
            {
              id: source.id,
              agentId: fixtureAgent,
              health: 'healthy',
              accessEpoch: source.accessEpoch,
            },
            { $set: { health: 'paused' } },
          ),
        );
        ensure(changed.matchedCount === 1, 'SOURCE_PAUSE_CAS');
        restoreSource = restorePausedSource;
      },
      [200, 403, 404, 503],
    );
    await restoreSource();
  }
  report.passed = true;
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
    report.at = new Date().toISOString();
    report.providerTurns = providerStates.filter((state) => state.started).length;
    report.providerRequests = providerStates.reduce((sum, state) => sum + state.requests, 0);
    if (reportAllowed)
      fs.writeFileSync(reportPath, JSON.stringify(report, null, 2), { mode: 0o600 });
    await mongoose?.disconnect().catch(() => {});
    process.stdout.write(`${JSON.stringify(report)}\n`);
    process.exit(report.passed && report.cleaned ? 0 : 1);
  });
