const { createNativeKnowledgeAccess } = require('@librechat/api');
const { findFileById } = require('~/models');
const { getKnowledgeService } = require('~/server/services/Knowledge');

module.exports = createNativeKnowledgeAccess({
  activeFileIds: async () => (await getKnowledgeService()).activeFileIds(),
  getFile: findFileById,
});
