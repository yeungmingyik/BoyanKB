const { createLoadConfigModels, fetchModels } = require('@librechat/api');
const { getAppConfig } = require('./app');
const db = require('~/models');

const loadConfigModels = createLoadConfigModels({
  getAppConfig,
  getUserKeyValues: db.getUserKeyValues,
  getUserKeyExpiry: db.getUserKeyExpiry,
  fetchModels,
});

module.exports = loadConfigModels;
