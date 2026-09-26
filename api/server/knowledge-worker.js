const path = require('node:path');
require('module-alias')({ base: path.resolve(__dirname, '..') });
const { readFile, writeFile } = require('node:fs/promises');
const mongoose = require('mongoose');
const yaml = require('js-yaml');
const { runAsSystem } = require('@librechat/data-schemas');
const { connectDb } = require('~/db/connect');
const { getKnowledgeService } = require('~/server/services/Knowledge');

let stopping = false;
let wake;
let heartbeat;
for (const signal of ['SIGTERM', 'SIGINT']) {
  process.on(signal, () => {
    stopping = true;
    wake?.();
  });
}

async function main() {
  const config = yaml.load(await readFile(process.env.CONFIG_PATH || 'librechat.yaml', 'utf8'));
  if (!config?.knowledge?.enabled || !config.knowledge.sync?.enabled) {
    return;
  }
  await connectDb();
  await runAsSystem(async () => {
    const service = await getKnowledgeService(config.knowledge);
    const beat = () =>
      writeFile('data/knowledge-worker-heartbeat', String(Date.now()), { mode: 0o600 });
    await beat();
    heartbeat = setInterval(() => {
      beat().catch(() => {
        process.exitCode = 1;
        stopping = true;
        wake?.();
      });
    }, 15000);
    heartbeat.unref();
    while (!stopping) {
      try {
        await service.tick();
      } catch (error) {
        const code = /^KNOWLEDGE_[A-Z_]+$/.test(error?.code || '')
          ? error.code
          : 'KNOWLEDGE_WORKER_FAILED';
        process.stderr.write(`${JSON.stringify({ code, at: new Date().toISOString() })}\n`);
      }
      if (!stopping) {
        await new Promise((resolve) => {
          const timer = setTimeout(resolve, 5000);
          wake = () => {
            clearTimeout(timer);
            resolve();
          };
        });
      }
    }
  });
}

main()
  .catch(() => {
    process.stderr.write('KNOWLEDGE_WORKER_START_FAILED\n');
    process.exitCode = 1;
  })
  .finally(() => {
    clearInterval(heartbeat);
    return mongoose.disconnect();
  });
