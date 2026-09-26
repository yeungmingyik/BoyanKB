const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { randomBytes, randomUUID } = require('node:crypto');
const { spawn } = require('node:child_process');
const { createServer } = require('node:http');

assert.equal(process.env.BOYANKB_TEST_INSTANCE, 'boyankb-librechat-test');
assert.equal(process.env.BOYANKB_KNOWLEDGE_AGENT_ID, 'agent_acceptance');
assert(!process.env.EMAIL_HOST && !process.env.EMAIL_SERVICE && !process.env.MAILGUN_API_KEY);

require('module-alias')({ base: path.resolve(process.cwd(), 'api') });
const mongoose = require('mongoose');
const { connectDb } = require('~/db');
const db = require('~/models');
const { registerUser } = require('~/server/services/AuthService');
const { createInvite, keyvMongo } = require('@librechat/api');
const { Keyv } = require('keyv');
const {
  PermissionBits,
  PrincipalType,
  AccessRoleIds,
  ViolationTypes,
} = require('librechat-data-provider');
const getLogStores = require('~/cache/getLogStores');

const checks = [];
const accountsFile = '/app/data/browser-smoke-accounts.json';
const reportsFile = '/app/data/access-integration-results.json';
const providerRequests = [];
const modelReply = 'BoyanKB protocol fixture response';
const provider = createServer(async (req, res) => {
  try {
    let input = '';
    for await (const chunk of req) {
      input += chunk;
    }
    const body = input ? JSON.parse(input) : {};
    if (req.method !== 'POST' || !req.url.endsWith('/chat/completions')) {
      res.writeHead(404).end();
      return;
    }
    providerRequests.push({ authorization: req.headers.authorization, model: body.model });
    const completion = {
      id: `chatcmpl-${randomUUID()}`,
      created: Math.floor(Date.now() / 1000),
      model: body.model,
    };
    if (!body.stream) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(
        JSON.stringify({
          ...completion,
          object: 'chat.completion',
          choices: [
            {
              index: 0,
              message: { role: 'assistant', content: modelReply },
              finish_reason: 'stop',
            },
          ],
          usage: { prompt_tokens: 8, completion_tokens: 6, total_tokens: 14 },
        }),
      );
      return;
    }
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
    for (const delta of [{ role: 'assistant', content: '' }, { content: modelReply }]) {
      res.write(
        `data: ${JSON.stringify({ ...completion, object: 'chat.completion.chunk', choices: [{ index: 0, delta, finish_reason: null }] })}\n\n`,
      );
    }
    res.write(
      `data: ${JSON.stringify({ ...completion, object: 'chat.completion.chunk', choices: [{ index: 0, delta: {}, finish_reason: 'stop' }], usage: { prompt_tokens: 8, completion_tokens: 6, total_tokens: 14 } })}\n\n`,
    );
    res.end('data: [DONE]\n\n');
  } catch {
    if (!res.headersSent) {
      res.writeHead(500);
    }
    res.end();
  }
});
const identities = ['admin', 'denied', 'authorized', 'other', 'banned', 'invited'];
const fixtures = fs.existsSync(accountsFile)
  ? JSON.parse(fs.readFileSync(accountsFile, 'utf8'))
  : {};
for (const name of identities) {
  fixtures[name] ??= {
    email: `${name}@boyankb-acceptance.invalid`,
    password: randomBytes(24).toString('hex'),
  };
}
fixtures.agentName = 'BoyanKB Acceptance';
fixtures.byokEndpoint = 'DeepSeek';
fs.writeFileSync(accountsFile, JSON.stringify(fixtures), { mode: 0o600 });

function pass(name) {
  checks.push(name);
  console.log(`PASS ${name}`);
}

async function request(route, { method = 'GET', session, body, cookie } = {}) {
  const headers = {
    Origin: process.env.DOMAIN_CLIENT,
    'Content-Type': 'application/json',
    'User-Agent':
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36',
  };
  if (session?.token) {
    headers.Authorization = `Bearer ${session.token}`;
  }
  if (cookie) {
    headers.Cookie = cookie;
  }
  const response = await fetch(`http://127.0.0.1:3080${route}`, {
    method,
    headers,
    redirect: 'manual',
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal: AbortSignal.timeout(30_000),
  });
  const text = await response.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = text;
  }
  return {
    status: response.status,
    data,
    text,
    cookie: response.headers
      .getSetCookie()
      .map((value) => value.split(';')[0])
      .join('; '),
  };
}

function status(response, expected, name) {
  assert.equal(response.status, expected, `${name}: HTTP ${response.status}`);
}

async function login(name) {
  const response = await request('/api/auth/login', { method: 'POST', body: fixtures[name] });
  status(response, 200, `${name} login`);
  assert(typeof response.data.token === 'string', 'Access token unavailable.');
  assert(/(?:^|; )refreshToken=/.test(response.cookie), 'Refresh cookie unavailable.');
  return { token: response.data.token, cookie: response.cookie, user: response.data.user };
}

async function main() {
  await connectDb();
  const users = mongoose.models.User;
  assert.equal(await users.countDocuments({ email: { $not: /@boyankb-acceptance\.invalid$/ } }), 0);
  await new Promise((resolve, reject) => {
    provider.once('error', reject);
    provider.listen(3099, '127.0.0.1', resolve);
  });
  provider.unref();
  const banCache = new Keyv({ store: keyvMongo, namespace: ViolationTypes.BAN, ttl: 0 });
  await getLogStores(ViolationTypes.BAN).delete('127.0.0.1');
  await banCache.delete('127.0.0.1');
  for (const user of await users.find({}, { _id: 1 }).lean()) {
    await getLogStores(ViolationTypes.BAN).delete(user._id.toString());
    await banCache.delete(user._id.toString());
  }
  const priorInviteUser = await users.findOne({ email: fixtures.invited.email }).lean();
  if (priorInviteUser) {
    await mongoose.models.Session.deleteMany({ user: priorInviteUser._id });
    await users.deleteOne({ _id: priorInviteUser._id });
  }

  for (const name of identities.filter((value) => value !== 'invited')) {
    if (await db.findUser({ email: fixtures[name].email })) {
      continue;
    }
    const result = await registerUser(
      {
        ...fixtures[name],
        name: `Acceptance ${name}`,
        username: `acceptance-${name}`,
        confirm_password: fixtures[name].password,
      },
      { emailVerified: true },
    );
    assert.equal(result.status, 200, `${name} account provisioning failed.`);
  }

  const sessions = {};
  for (const name of identities.filter((value) => value !== 'invited')) {
    sessions[name] = await login(name);
  }
  assert.equal(sessions.admin.user.role, 'ADMIN');
  assert.equal(sessions.authorized.user.role, 'USER');
  pass('native account provisioning and login');

  let agent = await db.getAgent({ id: 'agent_acceptance' });
  if (!agent) {
    agent = await db.createAgent({
      id: 'agent_acceptance',
      name: fixtures.agentName,
      author: sessions.admin.user.id,
      provider: 'DeepSeek',
      model: 'deepseek-flash',
      instructions: 'Answer the question.',
      tools: [],
    });
  }
  const agentResourceId = agent._id.toString();
  await db.grantPermission(
    PrincipalType.USER,
    new mongoose.Types.ObjectId(sessions.admin.user.id),
    'agent',
    agent._id,
    PermissionBits.VIEW | PermissionBits.EDIT | PermissionBits.DELETE | PermissionBits.SHARE,
    new mongoose.Types.ObjectId(sessions.admin.user.id),
  );

  const permissionRoute = `/api/permissions/agent/${agentResourceId}`;
  const grant = async (name) => {
    const response = await request(permissionRoute, {
      method: 'PUT',
      session: sessions.admin,
      body: {
        updated: [
          { type: 'user', id: sessions[name].user.id, accessRoleId: AccessRoleIds.AGENT_VIEWER },
        ],
        removed: [],
        public: false,
      },
    });
    status(response, 200, `${name} VIEW grant`);
    assert(
      Array.isArray(response.data?.results?.principals),
      'VIEW grant did not update permissions.',
    );
  };

  status(await request('/api/convos'), 401, 'Unauthenticated conversations');
  status(
    await request('/api/convos', { session: sessions.denied }),
    403,
    'Unapproved conversations',
  );
  status(await request('/api/keys', { session: sessions.denied }), 403, 'Unapproved keys');
  const deniedStatus = await request('/api/knowledge/access', { session: sessions.denied });
  status(deniedStatus, 200, 'Unapproved access status');
  assert.equal(deniedStatus.data.access, false);
  assert.equal(deniedStatus.data.configured, true);
  pass('login required and unapproved users denied');

  const registration = {
    email: fixtures.invited.email,
    name: 'Acceptance invited',
    username: 'acceptance-invited',
    password: fixtures.invited.password,
    confirm_password: fixtures.invited.password,
  };
  status(
    await request('/api/auth/register', { method: 'POST', body: registration }),
    403,
    'Closed registration',
  );
  if (!(await db.findUser({ email: fixtures.invited.email }))) {
    const token = await createInvite(fixtures.invited.email, { createToken: db.createToken });
    assert.equal(typeof token, 'string', 'Invite token unavailable.');
    status(
      await request('/api/auth/register', {
        method: 'POST',
        body: { ...registration, email: 'mismatch@boyankb-acceptance.invalid', token },
      }),
      400,
      'Invite email binding',
    );
    status(
      await request('/api/auth/register', {
        method: 'POST',
        body: { ...registration, token },
      }),
      200,
      'Invited registration',
    );
    status(
      await request('/api/auth/register', {
        method: 'POST',
        body: { ...registration, token },
      }),
      400,
      'Consumed invite replay',
    );
  }
  sessions.invited = await login('invited');
  status(
    await request('/api/convos', { session: sessions.invited }),
    403,
    'Invite does not grant VIEW',
  );
  pass('closed registration and email-bound one-use invitation');

  for (const name of ['authorized', 'other', 'banned']) {
    await grant(name);
  }
  for (const name of ['authorized', 'other']) {
    status(
      await request('/api/convos', { session: sessions[name] }),
      200,
      'Approved conversations',
    );
    const access = await request('/api/knowledge/access', { session: sessions[name] });
    assert.equal(access.data.access, true);
  }
  status(
    await request(permissionRoute, {
      method: 'PUT',
      session: sessions.admin,
      body: {
        updated: [{ type: 'role', id: 'USER', accessRoleId: AccessRoleIds.AGENT_VIEWER }],
        public: false,
      },
    }),
    400,
    'Role-wide VIEW rejected',
  );
  status(
    await request(permissionRoute, {
      method: 'PUT',
      session: sessions.admin,
      body: { updated: [], public: true },
    }),
    403,
    'Public VIEW rejected',
  );
  status(
    await request('/api/agents', {
      method: 'POST',
      session: sessions.authorized,
      body: { name: 'Forbidden' },
    }),
    403,
    'Partner cannot create Agent',
  );
  status(
    await request(permissionRoute, {
      method: 'PUT',
      session: sessions.authorized,
      body: { updated: [], public: true },
    }),
    403,
    'Partner cannot share Agent',
  );
  pass('individual VIEW grants and admin-only sharing');

  const keyA = `acceptance-a-${randomBytes(16).toString('hex')}`;
  const keyB = `acceptance-b-${randomBytes(16).toString('hex')}`;
  for (const [name, key, model] of [
    ['authorized', keyA, 'acceptance-model-a'],
    ['other', keyB, 'acceptance-model-b'],
  ]) {
    status(
      await request('/api/keys', {
        method: 'PUT',
        session: sessions[name],
        body: {
          name: 'DeepSeek',
          value: JSON.stringify({ apiKey: key, models: [model] }),
          userId: sessions.authorized.user.id,
        },
      }),
      201,
      `${name} key write`,
    );
  }
  const storedA = await mongoose.models.Key.findOne({
    userId: sessions.authorized.user.id,
    name: 'DeepSeek',
  }).lean();
  const storedB = await mongoose.models.Key.findOne({
    userId: sessions.other.user.id,
    name: 'DeepSeek',
  }).lean();
  assert(storedA && storedB && storedA.value !== storedB.value, 'Keys not user-isolated.');
  assert(
    !storedA.value.includes(keyA) && !storedB.value.includes(keyB),
    'Plaintext key persisted.',
  );
  const decrypted = await db.getUserKeyValues({
    userId: sessions.authorized.user.id,
    name: 'DeepSeek',
  });
  assert(decrypted.apiKey === keyA, 'Key round-trip failed.');
  const keyRead = await request(`/api/keys?name=DeepSeek&userId=${sessions.authorized.user.id}`, {
    session: sessions.other,
  });
  status(keyRead, 200, 'Key metadata');
  assert(
    !keyRead.text.includes(keyA) && !keyRead.text.includes(keyB),
    'Key response exposed a secret.',
  );
  const modelsA = await request('/api/models', { session: sessions.authorized });
  const modelsB = await request('/api/models', { session: sessions.other });
  status(modelsA, 200, 'Personal model directory A');
  status(modelsB, 200, 'Personal model directory B');
  assert(
    modelsA.data.DeepSeek.includes('acceptance-model-a') &&
      !modelsA.data.DeepSeek.includes('acceptance-model-b'),
    'Personal model directory A not isolated.',
  );
  assert(
    modelsB.data.DeepSeek.includes('acceptance-model-b') &&
      !modelsB.data.DeepSeek.includes('acceptance-model-a'),
    'Personal model directory B not isolated.',
  );
  status(
    await request(`/api/keys/DeepSeek?userId=${sessions.authorized.user.id}`, {
      method: 'DELETE',
      session: sessions.other,
    }),
    204,
    'Own key deletion',
  );
  assert(
    await mongoose.models.Key.exists({ userId: sessions.authorized.user.id, name: 'DeepSeek' }),
    'Other user key was deleted.',
  );
  assert(
    !(await mongoose.models.Key.exists({ userId: sessions.other.user.id, name: 'DeepSeek' })),
    'Own key not deleted.',
  );
  pass('encrypted BYOK storage, HTTP key and personal model isolation');

  const nativeMessageId = randomUUID();
  const nativeStart = await request('/api/agents/chat/DeepSeek', {
    method: 'POST',
    session: sessions.authorized,
    body: {
      text: 'Return the test response.',
      sender: 'User',
      clientTimestamp: new Date().toISOString(),
      isCreatedByUser: true,
      parentMessageId: '00000000-0000-0000-0000-000000000000',
      conversationId: 'new',
      messageId: nativeMessageId,
      responseMessageId: `${nativeMessageId}_response`,
      endpoint: 'DeepSeek',
      endpointType: 'custom',
      model: 'acceptance-model-a',
      isTemporary: false,
      isRegenerate: false,
      error: false,
    },
  });
  status(nativeStart, 200, 'Native model turn');
  assert.equal(nativeStart.data.status, 'started', 'Native model turn not started.');
  const nativeStream = await request(
    `/api/agents/chat/stream/${nativeStart.data.streamId}?resume=true`,
    {
      session: sessions.authorized,
    },
  );
  status(nativeStream, 200, 'Native model stream');
  assert(nativeStream.text.includes(modelReply), 'Native model stream response unavailable.');
  const nativeMessages = await request(`/api/messages/${nativeStart.data.conversationId}`, {
    session: sessions.authorized,
  });
  status(nativeMessages, 200, 'Native model messages');
  assert(
    JSON.stringify(nativeMessages.data).includes(modelReply),
    'Native model reply not persisted.',
  );
  assert(
    providerRequests.some(
      (entry) => entry.authorization === `Bearer ${keyA}` && entry.model === 'acceptance-model-a',
    ),
    'User credential or personal model not forwarded.',
  );
  status(
    await request(`/api/convos/${nativeStart.data.conversationId}`, {
      session: sessions.other,
    }),
    404,
    'Native model conversation isolation',
  );
  pass(
    'native model stream, personal credential forwarding and private persistence through local protocol stub',
  );

  status(
    await request('/api/convos', { session: sessions.other }),
    200,
    'Other user remains authorized',
  );
  status(
    await request('/api/keys', {
      method: 'PUT',
      session: sessions.other,
      body: {
        name: 'DeepSeek',
        value: JSON.stringify({ apiKey: keyB, models: ['acceptance-model-b'] }),
      },
    }),
    201,
    'Other user model credential',
  );
  const requestCount = providerRequests.length;
  const foreignMessageId = randomUUID();
  const foreignTurn = await request('/api/agents/chat/DeepSeek', {
    method: 'POST',
    session: sessions.other,
    body: {
      text: 'Continue this conversation.',
      sender: 'User',
      clientTimestamp: new Date().toISOString(),
      isCreatedByUser: true,
      parentMessageId: nativeMessages.data.find((message) => !message.isCreatedByUser).messageId,
      conversationId: nativeStart.data.conversationId,
      messageId: foreignMessageId,
      responseMessageId: `${foreignMessageId}_response`,
      endpoint: 'DeepSeek',
      endpointType: 'custom',
      model: 'acceptance-model-b',
      isTemporary: false,
      isRegenerate: false,
      error: false,
    },
  });
  assert(
    [403, 404].includes(foreignTurn.status),
    `Foreign continuation returned HTTP ${foreignTurn.status}`,
  );
  assert.equal(
    providerRequests.length,
    requestCount,
    'Foreign continuation reached model provider.',
  );
  assert(
    !(await mongoose.models.Message.exists({
      conversationId: nativeStart.data.conversationId,
      user: sessions.other.user.id,
    })),
    'Foreign continuation wrote messages',
  );
  status(
    await request('/api/keys/DeepSeek', { method: 'DELETE', session: sessions.other }),
    204,
    'Other test key cleanup',
  );

  const conversationId = randomUUID();
  const messageId = randomUUID();
  await db.saveConvo(
    { userId: sessions.authorized.user.id },
    {
      conversationId,
      title: 'Acceptance private conversation',
      endpoint: 'DeepSeek',
      model: 'deepseek-flash',
    },
  );
  await db.saveMessage(
    { userId: sessions.authorized.user.id },
    {
      messageId,
      conversationId,
      parentMessageId: '00000000-0000-0000-0000-000000000000',
      sender: 'User',
      text: 'Acceptance private content',
      isCreatedByUser: true,
      endpoint: 'DeepSeek',
      model: 'deepseek-flash',
    },
  );
  status(
    await request(`/api/convos/${conversationId}`, { session: sessions.authorized }),
    200,
    'Own conversation',
  );
  for (const name of ['other', 'admin']) {
    status(
      await request(`/api/convos/${conversationId}`, { session: sessions[name] }),
      404,
      'Foreign conversation',
    );
  }
  const foreignMessages = await request(`/api/messages/${conversationId}`, {
    session: sessions.other,
  });
  assert(
    [403, 404].includes(foreignMessages.status) ||
      (foreignMessages.status === 200 &&
        Array.isArray(foreignMessages.data) &&
        foreignMessages.data.length === 0),
    'Foreign messages accessible.',
  );
  const foreignUpdate = await request('/api/convos/update', {
    method: 'POST',
    session: sessions.other,
    body: { arg: { conversationId, title: 'Changed' } },
  });
  assert(
    [403, 404].includes(foreignUpdate.status),
    `Foreign rename returned HTTP ${foreignUpdate.status}`,
  );
  await request('/api/convos', {
    method: 'DELETE',
    session: sessions.other,
    body: { arg: { conversationId } },
  });
  const retained = await db.getConvo(sessions.authorized.user.id, conversationId);
  assert(
    retained && retained.title === 'Acceptance private conversation',
    'Foreign mutation changed conversation.',
  );
  status(
    await request('/api/convos/update', {
      method: 'POST',
      session: sessions.authorized,
      body: { arg: { conversationId, title: 'Acceptance updated' } },
    }),
    201,
    'Own rename',
  );
  pass('private conversation and message read/write isolation');

  assert(
    (await mongoose.models.Session.countDocuments({ user: sessions.authorized.user.id })) > 0,
    'Refresh session missing before revocation.',
  );
  status(
    await request(permissionRoute, {
      method: 'PUT',
      session: sessions.admin,
      body: {
        updated: [],
        removed: [{ type: 'user', id: sessions.authorized.user.id }],
        public: false,
      },
    }),
    200,
    'Revoke VIEW',
  );
  assert.equal(
    await mongoose.models.Session.countDocuments({ user: sessions.authorized.user.id }),
    0,
    'Refresh sessions survive revocation.',
  );
  for (const route of [
    '/api/convos',
    `/api/convos/${conversationId}`,
    `/api/messages/${conversationId}`,
    '/api/keys?name=DeepSeek',
    '/api/models',
  ]) {
    status(await request(route, { session: sessions.authorized }), 403, 'Revoked access JWT');
  }
  const refresh = await request('/api/auth/refresh', {
    method: 'POST',
    cookie: sessions.authorized.cookie,
  });
  assert([401, 403].includes(refresh.status), 'Revoked refresh token issued access.');
  await grant('authorized');
  sessions.authorized = await login('authorized');
  status(
    await request(`/api/convos/${conversationId}`, { session: sessions.authorized }),
    200,
    'Reauthorized history',
  );
  status(
    await request('/api/keys/DeepSeek', { method: 'DELETE', session: sessions.authorized }),
    204,
    'Test key cleanup',
  );
  pass('VIEW revocation rejects old JWT and refresh sessions');

  const banExit = await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ['config/ban-user.js', fixtures.banned.email, '60000'], {
      cwd: process.cwd(),
      env: process.env,
      stdio: 'ignore',
      timeout: 30_000,
    });
    child.once('error', reject);
    child.once('exit', resolve);
  });
  assert.equal(banExit, 0, 'Native ban-user command failed.');
  assert.equal(
    await mongoose.models.Session.countDocuments({ user: sessions.banned.user.id }),
    0,
    'Banned refresh sessions survive.',
  );
  status(await request('/api/convos', { session: sessions.banned }), 403, 'Banned access JWT');
  status(
    await request('/api/convos', { session: sessions.authorized }),
    200,
    'Other user sharing banned IP',
  );
  const unaffectedAccess = await request('/api/knowledge/access', { session: sessions.authorized });
  status(unaffectedAccess, 200, 'Other user knowledge access after ban');
  assert.equal(unaffectedAccess.data.access, true);
  pass('native timed ban, old JWT rejection and same-IP user isolation');

  fs.writeFileSync(
    reportsFile,
    JSON.stringify({ passed: true, checks, at: new Date().toISOString() }, null, 2),
    { mode: 0o600 },
  );
  await new Promise((resolve) => provider.close(resolve));
}

main()
  .then(async () => {
    await mongoose.disconnect();
    process.exit(0);
  })
  .catch(async (error) => {
    provider.close();
    fs.writeFileSync(
      reportsFile,
      JSON.stringify(
        { passed: false, checks, failure: error.message, at: new Date().toISOString() },
        null,
        2,
      ),
      { mode: 0o600 },
    );
    console.error(`FAIL ${error.message}`);
    await mongoose.disconnect().catch(() => {});
    process.exit(1);
  });
