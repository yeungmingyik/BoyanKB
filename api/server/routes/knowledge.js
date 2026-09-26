const { createKnowledgeRouter } = require('@librechat/api');
const { requireJwtAuth, checkBan } = require('~/server/middleware');
const knowledgeAccess = require('~/server/middleware/knowledgeAccess');
const { getKnowledgeService } = require('~/server/services/Knowledge');

module.exports = createKnowledgeRouter({
  getService: getKnowledgeService,
  authorize: (req, res, next) =>
    requireJwtAuth(req, res, (error) => {
      if (error) {
        return next(error);
      }
      checkBan(req, res, (banError) => {
        if (banError) {
          return next(banError);
        }
        return knowledgeAccess.authorize(req, res, (accessError) => {
          if (accessError) {
            return next(accessError);
          }
          if (!res.locals.knowledgeEnabled) {
            return res.status(404).json({ code: 'KNOWLEDGE_UNAVAILABLE' });
          }
          return next();
        });
      });
    }),
});
