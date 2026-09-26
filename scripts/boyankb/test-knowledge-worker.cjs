const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { performance } = require('node:perf_hooks');
const { setTimeout: delay } = require('node:timers/promises');

const fixtureAgent = 'agent_worker_acceptance';
const fixtureSpace = 'space_worker_acceptance';
const missingAgent = 'agent_worker_acceptance_missing';
const reportPath = '/app/data/knowledge-worker-integration-results.json';
const report = { passed: false, checks: [], processes: [], cleaned: false };
const children = [];
let mongoose;
let models;
let runAsSystem;
let sourceId;
let privateDirectory;
let fixtureOwner;
let ownsFixture = false;
let environmentValidated = false;

function ensure(condition, code) {
  if (!condition) {
    throw new Error(`KNOWLEDGE_WORKER_TEST_${code}`);
  }
}

function safeCode(error) {
  const value = error?.code ?? error?.message;
  return typeof value === 'string' && /^KNOWLEDGE_WORKER_TEST_[A-Z_]+$/.test(value)
    ? value
    : 'KNOWLEDGE_WORKER_TEST_FAILED';
}

function startWorker(configPath, preloadPath) {
  const child = spawn(
    process.execPath,
    ['--require', preloadPath, '/app/api/server/knowledge-worker.js'],
    {
      cwd: privateDirectory,
      env: {
        ...process.env,
        NODE_OPTIONS: '',
        CONFIG_PATH: configPath,
        BOYANKB_KNOWLEDGE_AGENT_ID: fixtureAgent,
        BOYANKB_KNOWLEDGE_STORAGE_DIR: path.join(privateDirectory, 'data', 'knowledge'),
        RAG_API_URL: 'http://127.0.0.1:1',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  );
  const state = { child, closed: false, stdout: '', stderr: '', started: performance.now() };
  state.finished = new Promise((resolve) => {
    child.once('error', () => {
      state.spawnFailed = true;
    });
    child.once('close', (exitCode, signal) => {
      state.closed = true;
      state.exitCode = exitCode;
      state.signal = signal;
      state.elapsedMs = Math.round(performance.now() - state.started);
      resolve();
    });
  });
  for (const stream of ['stdout', 'stderr']) {
    child[stream].setEncoding('utf8');
    child[stream].on('data', (data) => {
      state[stream] = (state[stream] + data).slice(-65536);
    });
  }
  children.push(state);
  return state;
}

async function finishWithin(state, timeoutMs, code) {
  let timer;
  try {
    await Promise.race([
      state.finished,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(`KNOWLEDGE_WORKER_TEST_${code}`)), timeoutMs);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
  ensure(!state.spawnFailed, 'PROCESS_SPAWN_FAILED');
}

function writeConfig(filename, agentId) {
  const yaml = require('js-yaml');
  const configPath = path.join(privateDirectory, filename);
  fs.writeFileSync(
    configPath,
    yaml.dump({
      version: '1.3.6',
      knowledge: {
        enabled: true,
        agentId,
        sync: {
          enabled: true,
          wikiUrl: 'https://fixture.feishu.cn/wiki/worker_acceptance',
          spaceId: fixtureSpace,
          pollIntervalMs: 60000,
          reconcileIntervalMs: 60000,
          leaseMs: 30000,
          maxRetries: 0,
          requestTimeoutMs: 1000,
        },
      },
    }),
    { mode: 0o600 },
  );
  return configPath;
}

async function cleanup() {
  for (const state of children) {
    if (!state.closed) {
      state.child.kill('SIGTERM');
      try {
        await finishWithin(state, 5000, 'CLEANUP_STOP_TIMEOUT');
      } catch {
        state.child.kill('SIGKILL');
        await finishWithin(state, 5000, 'CLEANUP_KILL_TIMEOUT');
      }
    }
  }
  if (ownsFixture) {
    await runAsSystem(async () => {
      for (const name of [
        'KnowledgeNode',
        'KnowledgeDocument',
        'KnowledgeRevision',
        'KnowledgeAsset',
        'KnowledgeItem',
        'KnowledgeRun',
        'KnowledgeAudit',
      ]) {
        await models[name].deleteMany({ sourceId });
      }
      await models.KnowledgeSource.deleteOne({ id: sourceId, agentId: fixtureAgent });
      await models.Agent.deleteOne({ id: fixtureAgent, author: fixtureOwner });
      ensure(
        !(await models.Agent.exists({ id: fixtureAgent })) &&
          !(await models.KnowledgeSource.exists({ id: sourceId })),
        'CLEANUP_DATABASE_FAILED',
      );
    });
  }
  if (privateDirectory) {
    const target = fs.realpathSync(privateDirectory);
    const parent = fs.realpathSync('/app/data');
    ensure(
      path.dirname(target) === parent && path.basename(target).startsWith('knowledge-worker-test-'),
      'CLEANUP_PATH_INVALID',
    );
    fs.rmSync(target, { recursive: true, force: true });
  }
  report.cleaned = true;
}

async function main() {
  ensure(process.env.NODE_ENV === 'test', 'ENVIRONMENT_INVALID');
  ensure(process.env.BOYANKB_TEST_INSTANCE === 'boyankb-librechat-sync-test', 'INSTANCE_INVALID');
  ensure(process.platform === 'linux' && process.cwd() === '/app', 'RUNTIME_INVALID');
  ensure(
    process.env.FEISHU_APP_ID === 'fixture_app' &&
      process.env.FEISHU_APP_SECRET === 'fixture_secret',
    'SYNTHETIC_CREDENTIALS_REQUIRED',
  );
  ensure(
    !process.env.EMAIL_HOST && !process.env.EMAIL_SERVICE && !process.env.MAILGUN_API_KEY,
    'EMAIL_DISABLED_REQUIRED',
  );
  let mongo;
  try {
    mongo = new URL(process.env.MONGO_URI);
  } catch {
    ensure(false, 'DATABASE_ORIGIN_INVALID');
  }
  ensure(
    mongo.protocol === 'mongodb:' &&
      mongo.hostname === 'mongodb' &&
      (!mongo.port || mongo.port === '27017') &&
      mongo.pathname.length > 1,
    'DATABASE_ORIGIN_INVALID',
  );
  environmentValidated = true;
  mongoose = require('mongoose');
  const schemas = require('@librechat/data-schemas');
  const { knowledgeId } = require('@librechat/api');
  runAsSystem = schemas.runAsSystem;
  models = schemas.createModels(mongoose);
  await mongoose.connect(process.env.MONGO_URI, { serverSelectionTimeoutMS: 10000 });
  await runAsSystem(async () => {
    ensure(
      (await models.User.countDocuments({ email: { $not: /@boyankb-acceptance\.invalid$/ } })) ===
        0,
      'SYNTHETIC_DATABASE_REQUIRED',
    );
    const owner = await models.User.findOne({
      email: 'sync-admin@boyankb-acceptance.invalid',
      role: 'ADMIN',
    }).lean();
    ensure(owner, 'SYNTHETIC_ADMIN_REQUIRED');
    fixtureOwner = owner._id;
    sourceId = knowledgeId('source', fixtureSpace);
    ensure(
      !(await models.Agent.exists({ id: { $in: [fixtureAgent, missingAgent] } })) &&
        !(await models.KnowledgeSource.exists({
          $or: [{ id: sourceId }, { agentId: fixtureAgent }, { spaceId: fixtureSpace }],
        })),
      'FIXTURE_ALREADY_EXISTS',
    );
    const hello = await mongoose.connection.db.admin().command({ hello: 1 });
    ensure(hello.setName && hello.isWritablePrimary, 'REPLICA_PRIMARY_REQUIRED');
    const mainAgent = await models.Agent.findOne({ id: 'agent_sync_acceptance' })
      .select('_id author')
      .lean();
    const mainSource = await models.KnowledgeSource.findOne({ agentId: 'agent_sync_acceptance' })
      .select('id')
      .lean();
    ensure(mainAgent && mainSource, 'MAIN_FIXTURE_REQUIRED');

    await models.Agent.create({
      id: fixtureAgent,
      name: 'Synthetic worker acceptance',
      provider: 'DeepSeek',
      model: 'synthetic-model',
      author: fixtureOwner,
      tools: [],
    });
    ownsFixture = true;
    privateDirectory = fs.mkdtempSync('/app/data/knowledge-worker-test-');
    fs.chmodSync(privateDirectory, 0o700);
    fs.mkdirSync(path.join(privateDirectory, 'data'), { mode: 0o700 });
    fs.mkdirSync(path.join(privateDirectory, 'logs'), { mode: 0o700 });
    const preloadPath = path.join(privateDirectory, 'deny-feishu.cjs');
    fs.writeFileSync(
      preloadPath,
      [
        "const { FeishuClient, FeishuError } = require('@librechat/api');",
        'FeishuClient.prototype.resolveSpace = async function () {',
        "  throw new FeishuError('source_denied', 'source');",
        '};',
        '',
      ].join('\n'),
      { mode: 0o600 },
    );

    const worker = startWorker(writeConfig('worker.yaml', fixtureAgent), preloadPath);
    const deadline = Date.now() + 45000;
    let paused = false;
    while (Date.now() < deadline) {
      ensure(!worker.closed, 'WORKER_EXITED_BEFORE_RUN');
      const [source, run] = await Promise.all([
        models.KnowledgeSource.findOne({ id: sourceId, agentId: fixtureAgent }).lean(),
        models.KnowledgeRun.findOne({ sourceId, status: 'failed' }).lean(),
      ]);
      if (source?.health === 'paused' && run?.errorCode === 'FEISHU_SOURCE_DENIED') {
        ensure(source.errorCode === 'FEISHU_SOURCE_DENIED', 'SOURCE_ERROR_INVALID');
        ensure(source.accessEpoch === 1 && run.finishedAt, 'PAUSE_NOT_COMMITTED');
        paused = true;
        break;
      }
      await delay(200);
    }
    ensure(paused, 'FAILED_RUN_TIMEOUT');
    report.checks.push('fresh_worker_initializes_models_and_commits_failed_run');
    const stoppedAt = performance.now();
    worker.child.kill('SIGTERM');
    await finishWithin(worker, 10000, 'SIGTERM_EXIT_TIMEOUT');
    ensure(worker.exitCode === 0 && worker.signal === null, 'SIGTERM_EXIT_INVALID');
    report.processes.push({
      scenario: 'source_denied',
      exitCode: worker.exitCode,
      elapsedMs: worker.elapsedMs,
      shutdownMs: Math.round(performance.now() - stoppedAt),
    });
    report.checks.push('sigterm_exits_zero_without_cache_timer_hang');

    const missing = startWorker(writeConfig('missing-agent.yaml', missingAgent), preloadPath);
    await finishWithin(missing, 15000, 'START_FAILURE_EXIT_TIMEOUT');
    ensure(missing.exitCode === 1 && missing.signal === null, 'START_FAILURE_EXIT_INVALID');
    ensure(
      missing.stderr.split(/\r?\n/).includes('KNOWLEDGE_WORKER_START_FAILED') &&
        !/TypeError|Cannot read properties|fixture_secret|mongodb:\/\//.test(missing.stderr),
      'START_FAILURE_NOT_SANITIZED',
    );
    report.processes.push({
      scenario: 'missing_agent',
      exitCode: missing.exitCode,
      elapsedMs: missing.elapsedMs,
    });
    report.checks.push('missing_agent_exits_one_with_static_error');
    ensure(
      (await models.KnowledgeDocument.countDocuments({ sourceId })) === 0 &&
        (await models.KnowledgeAsset.countDocuments({ sourceId })) === 0 &&
        !(await models.KnowledgeSource.exists({ agentId: missingAgent })),
      'UNEXPECTED_KNOWLEDGE_WRITES',
    );
    ensure(
      (await models.Agent.exists({
        _id: mainAgent._id,
        id: 'agent_sync_acceptance',
        author: mainAgent.author,
      })) &&
        (await models.KnowledgeSource.exists({
          id: mainSource.id,
          agentId: 'agent_sync_acceptance',
        })),
      'MAIN_FIXTURE_CHANGED',
    );
    report.checks.push('main_fixture_preserved_without_content_or_index_writes');
  });
  report.passed = true;
}

report.startedAtUtc = new Date().toISOString();
main()
  .catch((error) => {
    report.errorCode = safeCode(error);
  })
  .finally(async () => {
    try {
      await cleanup();
    } catch (error) {
      report.passed = false;
      report.errorCode = safeCode(error);
    }
    await mongoose?.disconnect().catch(() => {});
    report.completedAtUtc = new Date().toISOString();
    if (environmentValidated) {
      fs.writeFileSync(reportPath, JSON.stringify(report, null, 2), { mode: 0o600 });
    }
    process.stdout.write(`${JSON.stringify(report)}\n`, () => process.exit(report.passed ? 0 : 1));
  });
