const {
  createKnowledgeAccess,
  createKnowledgeUserPermissionChecker,
  resolveKnowledgeConfig,
} = require('@librechat/api');
const { getAppConfig } = require('~/server/services/Config');
const { getAgent, hasPermission, deleteAllUserSessions } = require('~/models');

module.exports = createKnowledgeAccess({
  getConfig: async () =>
    resolveKnowledgeConfig((await getAppConfig({ baseOnly: true })).config?.knowledge),
  getAgent,
  hasUserPermission: createKnowledgeUserPermissionChecker(hasPermission),
  deleteUserSessions: deleteAllUserSessions,
});
