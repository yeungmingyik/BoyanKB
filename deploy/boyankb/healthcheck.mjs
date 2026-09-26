import { randomUUID } from 'node:crypto';
import { writeFile, unlink } from 'node:fs/promises';
import { MongoClient } from 'mongodb';

const client = new MongoClient(process.env.MONGO_URI, {
  serverSelectionTimeoutMS: 3000,
  connectTimeoutMS: 3000,
});

try {
  const response = await fetch('http://127.0.0.1:3080/readyz', {
    signal: AbortSignal.timeout(3000),
  });
  if (!response.ok) {
    throw new Error('APP_NOT_READY');
  }
  await client.connect();
  await client.db().command({ ping: 1 });
  for (const directory of ['/app/data', '/app/logs', '/app/uploads']) {
    const probe = `${directory}/.health-${randomUUID()}`;
    await writeFile(probe, '', { flag: 'wx', mode: 0o600 });
    await unlink(probe);
  }
} catch {
  process.exitCode = 1;
} finally {
  await client.close();
}
