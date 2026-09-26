const { createHash, randomUUID } = require('node:crypto');

const report = {
  passed: false,
  checks: [],
  embeddings: 0,
  files: 0,
  chunks: 0,
  cleaned: false,
};
const attempted = new Map();
const states = [];
let createNativeKnowledgeIndexer;
let generateShortLivedToken;

function ensure(condition, code) {
  if (!condition) {
    throw new Error(`KNOWLEDGE_RAG_TEST_${code}`);
  }
}

function pass(name) {
  report.checks.push(name);
}

function safeCode(error) {
  const value = error?.code ?? error?.message;
  return typeof value === 'string' && /^KNOWLEDGE_(?:RAG_TEST|INDEX)_[A-Z_]+$/.test(value)
    ? value
    : 'KNOWLEDGE_RAG_TEST_FAILED';
}

async function send(ownerId, path, init = {}) {
  const url = new URL(path, 'http://rag:8000');
  ensure(url.origin === 'http://rag:8000', 'ORIGIN_INVALID');
  return fetch(url, {
    ...init,
    headers: {
      ...(ownerId ? { Authorization: `Bearer ${generateShortLivedToken(ownerId)}` } : {}),
      ...init.headers,
    },
    redirect: 'error',
    signal: AbortSignal.timeout(180_000),
  });
}

async function read(ownerId, fileId, entityId) {
  const query = new URLSearchParams({ ids: fileId });
  if (entityId) {
    query.set('entity_id', entityId);
  }
  const response = await send(ownerId, `/documents?${query}`);
  if (!response.ok) {
    await response.body?.cancel();
    return { status: response.status, rows: [] };
  }
  const rows = await response.json();
  ensure(Array.isArray(rows), 'ROWS_INVALID');
  return { status: response.status, rows };
}

function state() {
  const ownerId = randomUUID();
  const agentId = `agent_boyankb_rag_test_${randomUUID().replaceAll('-', '')}`;
  const files = new Map();
  const fetcher = async (url, init) => {
    ensure(url.origin === 'http://rag:8000', 'ORIGIN_INVALID');
    if (url.pathname === '/embed') {
      const fileId = init.body.get('file_id');
      ensure(/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/.test(fileId), 'FILE_ID_INVALID');
      ensure(init.body.get('entity_id') === agentId, 'ENTITY_INVALID');
      attempted.set(fileId, { ownerId, agentId });
      report.embeddings++;
    }
    return fetch(url, init);
  };
  const callbacks = {
    findFile: async (filename) =>
      Array.from(files.values()).find((file) => file.filename === filename) ?? null,
    getFile: async (fileId) => files.get(fileId) ?? null,
    saveFile: async (file) => {
      ensure(file.user === ownerId, 'OWNER_INVALID');
      ensure(
        file.metadata.embeddedEntities.length === 1 &&
          file.metadata.embeddedEntities[0] === agentId,
        'ENTITY_INVALID',
      );
      files.set(file.file_id, file);
    },
    deleteFile: async (fileId) => files.delete(fileId),
  };
  const dependencies = {
    ...callbacks,
    agentId,
    ownerId,
    baseUrl: 'http://rag:8000',
    version: 'boyankb-rag-acceptance-v1',
    fetch: fetcher,
    timeoutMs: 180_000,
  };
  const value = {
    ownerId,
    agentId,
    files,
    dependencies,
    indexer: createNativeKnowledgeIndexer(dependencies),
  };
  states.push(value);
  return value;
}

async function cleanup() {
  let complete = true;
  for (const [fileId, target] of attempted) {
    try {
      ensure(target.agentId.startsWith('agent_boyankb_rag_test_'), 'CLEANUP_SCOPE_INVALID');
      const query = new URLSearchParams({ entity_id: target.agentId });
      const response = await send(target.ownerId, `/documents?${query}`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify([fileId]),
      });
      complete = complete && (response.ok || response.status === 404);
      await response.body?.cancel();
      const remaining = await read(target.ownerId, fileId, target.agentId);
      complete = complete && remaining.status === 404;
    } catch {
      complete = false;
    }
  }
  states.forEach((target) => target.files.clear());
  report.cleaned = complete;
  ensure(complete, 'CLEANUP_FAILED');
}

async function main() {
  ensure(process.env.NODE_ENV === 'test', 'ENVIRONMENT_INVALID');
  ensure(process.env.BOYANKB_TEST_INSTANCE === 'boyankb-librechat-sync-test', 'INSTANCE_INVALID');
  ensure(process.env.RAG_API_URL === 'http://rag:8000', 'ORIGIN_INVALID');
  ensure(
    typeof process.env.JWT_SECRET === 'string' && process.env.JWT_SECRET.length >= 32,
    'CREDENTIALS_MISSING',
  );
  ({ createNativeKnowledgeIndexer, generateShortLivedToken } = require('@librechat/api'));
  ensure(
    typeof createNativeKnowledgeIndexer === 'function' &&
      typeof generateShortLivedToken === 'function',
    'BUILD_INVALID',
  );
  const first = state();
  const second = state();
  const text =
    '合成验收资料：机器人课程包含传感器实验与编程练习。人工智能营地包含模型学习、动手制作和成果展示。';
  const input = {
    documentId: randomUUID(),
    revisionId: randomUUID(),
    title: '合成验收资料',
    text,
    idempotencyKey: createHash('sha256').update(text).digest('hex'),
  };

  try {
    const indexed = await first.indexer.index(input);
    ensure(indexed.fileIds.length === 1 && first.files.size === 1, 'INDEX_FAILED');
    const fileId = indexed.fileIds[0];
    const result = await read(first.ownerId, fileId, first.agentId);
    ensure(result.status === 200 && result.rows.length > 0, 'CHUNKS_MISSING');
    ensure(
      result.rows.every(
        (row) =>
          typeof row?.page_content === 'string' &&
          row.metadata?.file_id === fileId &&
          row.metadata?.user_id === first.agentId,
      ),
      'CHUNK_SCOPE_INVALID',
    );
    ensure(
      result.rows
        .map((row) => row.page_content)
        .join('\n')
        .includes('传感器实验'),
      'CHINESE_TEXT_MISSING',
    );
    report.chunks += result.rows.length;
    pass('native_embedding_and_entity_scope');

    const repeated = await first.indexer.index(input);
    ensure(
      repeated.fileIds.length === 1 &&
        repeated.fileIds[0] === fileId &&
        first.files.size === 1 &&
        report.embeddings === 1,
      'IDEMPOTENCY_FAILED',
    );
    const repeatedRows = await read(first.ownerId, fileId, first.agentId);
    ensure(repeatedRows.rows.length === result.rows.length, 'DUPLICATE_CHUNKS');
    pass('idempotent_file_and_chunks');

    const unauthenticated = await read(undefined, fileId, first.agentId);
    ensure([401, 403].includes(unauthenticated.status), 'AUTHENTICATION_MISSING');
    const foreign = await read(second.ownerId, fileId);
    ensure(foreign.status === 404, 'FOREIGN_OWNER_VISIBLE');
    const wrongEntity = await read(second.ownerId, fileId, second.agentId);
    ensure(wrongEntity.status === 404, 'FOREIGN_ENTITY_VISIBLE');
    const wrongOwner = createNativeKnowledgeIndexer({
      ...first.dependencies,
      ownerId: second.ownerId,
    });
    ensure(!(await wrongOwner.verify([fileId])), 'NATIVE_OWNER_SCOPE_INVALID');
    pass('anonymous_and_wrong_owner_rejected');

    const other = await second.indexer.index({
      ...input,
      documentId: randomUUID(),
      revisionId: randomUUID(),
      text: '另一组合成资料：学生完成无人机飞行任务。',
      idempotencyKey: 'synthetic_second_owner',
    });
    ensure(other.fileIds.length === 1 && second.files.size === 1, 'SECOND_INDEX_FAILED');
    const otherFile = other.fileIds[0];
    const otherBefore = await read(second.ownerId, otherFile, second.agentId);
    ensure(otherBefore.status === 200 && otherBefore.rows.length > 0, 'SECOND_CHUNKS_MISSING');
    report.chunks += otherBefore.rows.length;
    report.files = first.files.size + second.files.size;
    const foreignDelete = await send(
      first.ownerId,
      `/documents?${new URLSearchParams({ entity_id: first.agentId })}`,
      {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify([otherFile]),
      },
    );
    ensure(foreignDelete.status === 404, 'FOREIGN_DELETE_ACCEPTED');
    await foreignDelete.body?.cancel();
    pass('foreign_delete_rejected');

    await first.indexer.remove([fileId]);
    ensure(
      (await read(first.ownerId, fileId, first.agentId)).status === 404 && first.files.size === 0,
      'SCOPED_DELETE_FAILED',
    );
    const otherAfter = await read(second.ownerId, otherFile, second.agentId);
    ensure(
      otherAfter.status === 200 &&
        otherAfter.rows.length === otherBefore.rows.length &&
        (await second.indexer.verify([otherFile])),
      'OTHER_OWNER_CHANGED',
    );
    pass('explicit_entity_delete_preserves_other_owner');
  } finally {
    await cleanup();
  }
  report.passed = true;
}

main()
  .catch((error) => {
    report.errorCode = safeCode(error);
    process.exitCode = 1;
  })
  .finally(() => {
    process.stdout.write(`${JSON.stringify(report)}\n`);
  });
