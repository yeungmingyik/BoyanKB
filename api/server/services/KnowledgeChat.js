const {
  createKnowledgeAnswerContext,
  resolveKnowledgeQuestion,
  resolveKnowledgeConfig,
} = require('@librechat/api');
const { getKnowledgeService } = require('./Knowledge');
const { getMessages } = require('~/models');

async function prepareKnowledgeAnswer(req, requestBody = req.body) {
  const rawConfig = req.config?.config?.knowledge;
  if (!rawConfig?.enabled || !rawConfig.sync?.enabled) {
    return;
  }
  const config = resolveKnowledgeConfig(rawConfig);
  const service = await getKnowledgeService();
  const query = await resolveKnowledgeQuestion({
    text: req.body?.text,
    parentMessageId: requestBody?.parentMessageId ?? req.body?.parentMessageId,
    maxLength: config.search?.maxQueryChars,
    loadMessage: async (messageId) => {
      const rows = await getMessages(
        {
          user: req.user.id,
          conversationId: requestBody?.conversationId ?? req.body?.conversationId,
          messageId,
        },
        'messageId parentMessageId isCreatedByUser text',
        { limit: 1 },
      );
      return rows[0];
    },
  });
  const result = await service.search({
    query,
    mode: 'hybrid',
    limit: Math.min(config.search?.maxHits ?? 8, config.search?.maxResults ?? 20),
  });
  const context = createKnowledgeAnswerContext(result, config.search);
  req.knowledgeContext = { snapshotId: context.snapshotId, items: context.items };
  return context;
}

module.exports = { prepareKnowledgeAnswer };
