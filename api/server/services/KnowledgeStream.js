const generations = new Map();

function enabled(req) {
  return req.config?.config?.knowledge?.enabled === true;
}

function identity(streamId, createdAt) {
  return `${streamId}:${createdAt}`;
}

function projectContext(context) {
  if (context === undefined) {
    return undefined;
  }
  const { KnowledgeStreamError } = require('@librechat/api');
  if (
    !context ||
    typeof context.snapshotId !== 'string' ||
    !Array.isArray(context.items) ||
    context.items.length > 100
  ) {
    throw new KnowledgeStreamError('KNOWLEDGE_SOURCE_CHANGED');
  }
  return {
    snapshotId: context.snapshotId,
    items: context.items.map((item) => {
      if (
        !item ||
        !['documentId', 'revisionId', 'blockId'].every(
          (key) => typeof item[key] === 'string' && item[key].length > 0 && item[key].length <= 256,
        )
      ) {
        throw new KnowledgeStreamError('KNOWLEDGE_SOURCE_CHANGED');
      }
      return {
        documentId: item.documentId,
        revisionId: item.revisionId,
        blockId: item.blockId,
      };
    }),
  };
}

function dependencies(req, getContext) {
  const mongoose = require('mongoose');
  const { ViolationTypes } = require('librechat-data-provider');
  const {
    resolveKnowledgeConfig,
    createKnowledgeStreamAccessCheck,
    createKnowledgeUserPermissionChecker,
    KnowledgeStreamError,
  } = require('@librechat/api');
  const { hasPermission } = require('~/models');
  const { getLogStores } = require('~/cache');
  const { getKnowledgeService } = require('./Knowledge');
  const config = resolveKnowledgeConfig(req.config.config.knowledge);
  const userId = req.user?.id ?? req.user?._id?.toString();
  return {
    checkAccess: createKnowledgeStreamAccessCheck({
      userId,
      tenantId: req.user?.tenantId,
      agentId: config.agentId,
      getUser: (id) =>
        mongoose.models.User.findById(id).select('role tenantId').maxTimeMS(1000).lean(),
      getAgent: (id) => mongoose.models.Agent.findOne({ id }).select('_id').maxTimeMS(1000).lean(),
      getBans: (id) => {
        const store = getLogStores(ViolationTypes.BAN);
        return Promise.all([store.get(id), ...(req.ip ? [store.get(req.ip)] : [])]);
      },
      hasUserPermission: createKnowledgeUserPermissionChecker(hasPermission),
    }),
    assertKnowledgeCurrent: async () => {
      try {
        const service = await getKnowledgeService();
        const health = await service.getSourceHealth();
        if (health.sourceStatus !== 'ready') {
          throw new KnowledgeStreamError('KNOWLEDGE_SOURCE_UNAVAILABLE');
        }
        const context = projectContext(await getContext());
        if (context && !(await service.validateHits(context.items, context.snapshotId))) {
          throw new KnowledgeStreamError('KNOWLEDGE_SOURCE_CHANGED');
        }
      } catch (error) {
        throw error instanceof KnowledgeStreamError
          ? error
          : new KnowledgeStreamError('KNOWLEDGE_SOURCE_UNAVAILABLE');
      }
    },
  };
}

function finishKnowledgeGeneration(record) {
  if (!record || record.finished) {
    return;
  }
  record.finished = true;
  record.guard.dispose();
  try {
    record.context = projectContext(record.getContext());
  } catch {
    record.context = null;
  }
  record.getContext = () => record.context;
  const timer = setTimeout(() => {
    if (generations.get(record.key) === record) {
      generations.delete(record.key);
    }
  }, 60000);
  timer.unref?.();
}

async function startKnowledgeGeneration({ req, job, streamId }) {
  if (!enabled(req)) {
    return null;
  }
  const { createKnowledgeStreamGuard, GenerationJobManager } = require('@librechat/api');
  const key = identity(streamId, job.createdAt);
  const record = {
    key,
    userId: req.user.id,
    getContext: () => req.knowledgeContext,
    finished: false,
  };
  record.guard = createKnowledgeStreamGuard({
    ...dependencies(req, () => record.getContext()),
    abort: (error) => {
      job.abortController.abort(error);
      void GenerationJobManager.updateMetadata(
        streamId,
        { knowledgeStopCode: error.code },
        job.createdAt,
      ).catch(() => {});
      void GenerationJobManager.abortJob(streamId, {
        expectedCreatedAt: job.createdAt,
        transformAbortContent: () => [],
      }).catch(() => {});
    },
  });
  generations.set(key, record);
  try {
    await record.guard.start();
    return record;
  } catch (error) {
    finishKnowledgeGeneration(record);
    throw error;
  }
}

async function captureKnowledgeContext(req, record, streamId, createdAt) {
  if (!record) {
    return;
  }
  const { GenerationJobManager } = require('@librechat/api');
  await record.guard.checkNow();
  await GenerationJobManager.updateMetadata(
    streamId,
    { knowledgeContext: projectContext(req.knowledgeContext) },
    createdAt,
  );
}

async function attachKnowledgeStream({ req, job, streamId, onStop }) {
  if (!enabled(req)) {
    return null;
  }
  const {
    createKnowledgeStreamGuard,
    KnowledgeStreamError,
    GenerationJobManager,
  } = require('@librechat/api');
  const record = generations.get(identity(streamId, job.createdAt));
  let lastContext = projectContext(job.metadata?.knowledgeContext);
  const getContext = async () => {
    if (record) {
      if (record.userId !== req.user.id) {
        throw new KnowledgeStreamError('KNOWLEDGE_ACCESS_DENIED');
      }
      if (record.guard.reason) {
        throw record.guard.reason;
      }
      lastContext = projectContext(record.getContext());
      return lastContext;
    }
    const current = await GenerationJobManager.getJob(streamId);
    if (current) {
      if (current.createdAt !== job.createdAt || current.metadata?.userId !== req.user.id) {
        throw new KnowledgeStreamError('KNOWLEDGE_ACCESS_DENIED');
      }
      if (current.metadata?.knowledgeStopCode) {
        throw new KnowledgeStreamError('KNOWLEDGE_STREAM_CLOSED');
      }
      lastContext = projectContext(current.metadata?.knowledgeContext);
    }
    return lastContext;
  };
  const guard = createKnowledgeStreamGuard({
    ...dependencies(req, getContext),
    abort: (error) => {
      record?.guard.stop(error.code);
      void GenerationJobManager.abortJob(streamId, {
        expectedCreatedAt: job.createdAt,
        transformAbortContent: () => [],
      }).catch(() => {});
    },
    onStop,
  });
  const stopFromGeneration = () => {
    guard.stop(record.guard.reason?.code ?? 'KNOWLEDGE_STREAM_CLOSED');
  };
  record?.guard.signal.addEventListener('abort', stopFromGeneration, { once: true });
  const dispose = () => {
    guard.dispose();
    record?.guard.signal.removeEventListener('abort', stopFromGeneration);
  };
  try {
    if (job.metadata?.knowledgeStopCode || record?.guard.reason) {
      throw new KnowledgeStreamError('KNOWLEDGE_STREAM_CLOSED');
    }
    await guard.start();
    return {
      ...guard,
      get reason() {
        return guard.reason;
      },
      dispose,
    };
  } catch (error) {
    dispose();
    throw error;
  }
}

async function assertKnowledgeJobAccess(req, job, streamId) {
  const stream = await attachKnowledgeStream({ req, job, streamId });
  stream?.dispose();
}

module.exports = {
  startKnowledgeGeneration,
  captureKnowledgeContext,
  finishKnowledgeGeneration,
  attachKnowledgeStream,
  assertKnowledgeJobAccess,
};
