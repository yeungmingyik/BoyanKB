const path = require('node:path');
const mongoose = require('mongoose');
const { createModels, runAsSystem } = require('@librechat/data-schemas');
const {
  resolveKnowledgeConfig,
  createKnowledgeService,
  LocalKnowledgeBlobStore,
  FeishuClient,
  createNativeKnowledgeIndexer,
  KnowledgeError,
} = require('@librechat/api');
const { getAppConfig } = require('./Config');
const { getAgent, createFile, findFileById } = require('~/models');

let runtime;

async function initializeKnowledgeService(rawConfig) {
  const config = resolveKnowledgeConfig(rawConfig);
  if (!config?.enabled || !config.sync?.enabled || !config.agentId || !config.sync.wikiUrl) {
    throw new KnowledgeError('KNOWLEDGE_SYNC_NOT_CONFIGURED', 503);
  }
  const agent = await getAgent({ id: config.agentId });
  const models = createModels(mongoose);
  const owner = agent?.author
    ? await models.User.findById(agent.author).select('role').lean()
    : null;
  if (!owner || owner.role !== 'ADMIN') {
    throw new KnowledgeError('KNOWLEDGE_AGENT_NOT_CONFIGURED', 503);
  }
  const indexer = createNativeKnowledgeIndexer({
    agentId: config.agentId,
    ownerId: owner._id.toString(),
    baseUrl: process.env.RAG_API_URL || 'http://rag:8000',
    version: process.env.BOYANKB_INDEX_VERSION || 'bge-small-zh-v1.5-7999e1d-v1',
    findFile: (filename) =>
      models.File.findOne({ filename, context: 'knowledge', embedded: true }).lean(),
    getFile: findFileById,
    saveFile: (file) => createFile(file, true),
    deleteFile: (fileId) => models.File.deleteOne({ file_id: fileId, context: 'knowledge' }),
  });
  const service = createKnowledgeService({
    models,
    config: { agentId: config.agentId, sync: config.sync },
    blobs: new LocalKnowledgeBlobStore(
      process.env.BOYANKB_KNOWLEDGE_STORAGE_DIR || path.resolve('data/knowledge'),
    ),
    client: new FeishuClient({
      appId: process.env.FEISHU_APP_ID || '',
      appSecret: process.env.FEISHU_APP_SECRET || '',
      timeoutMs: config.sync.requestTimeoutMs,
      maxRetries: config.sync.maxRetries,
      maxMediaBytes: config.sync.maxAssetBytes,
      maxBlocks: config.sync.maxBlocks,
    }),
    indexer,
  });
  await service.ensureSource();
  return service;
}

async function getKnowledgeService(rawConfig) {
  if (!runtime) {
    runtime = runAsSystem(async () =>
      initializeKnowledgeService(
        rawConfig ?? (await getAppConfig({ baseOnly: true })).config?.knowledge,
      ),
    ).catch((error) => {
      runtime = undefined;
      throw error;
    });
  }
  return runtime;
}

module.exports = { getKnowledgeService };
