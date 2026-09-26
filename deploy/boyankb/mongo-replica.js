const admin = globalThis.db.getSiblingDB('admin');
if (
  !admin.auth(process.env.MONGO_INITDB_ROOT_USERNAME, process.env.MONGO_INITDB_ROOT_PASSWORD).ok
) {
  globalThis.quit(1);
}
let state;
try {
  state = admin.runCommand({ replSetGetStatus: 1 });
} catch (error) {
  if (error.code !== 94) {
    throw error;
  }
  state = { code: 94 };
}
if (state.code === 94) {
  const result = admin.runCommand({
    replSetInitiate: { _id: 'boyankb', members: [{ _id: 0, host: 'mongodb:27017' }] },
  });
  if (!result.ok) {
    globalThis.quit(1);
  }
} else if (!state.ok || state.set !== 'boyankb') {
  globalThis.quit(1);
}
let primary = false;
for (let attempt = 0; attempt < 60; attempt += 1) {
  if (admin.runCommand({ hello: 1 }).isWritablePrimary) {
    primary = true;
    break;
  }
  globalThis.sleep(1000);
}
if (!primary) {
  globalThis.quit(1);
}
