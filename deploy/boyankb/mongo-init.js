const database = globalThis.db.getSiblingDB('BoyanKB');

database.createUser({
  user: process.env.MONGO_APP_USERNAME,
  pwd: process.env.MONGO_APP_PASSWORD,
  roles: [{ role: 'readWrite', db: 'BoyanKB' }],
});
