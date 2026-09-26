const fs = require('node:fs');
const crypto = require('node:crypto');
const yaml = require('js-yaml');
const dotenv = require('dotenv');

const root = '/config';
const source = dotenv.parse(fs.readFileSync(`${root}/feishu.env`));
for (const key of ['FEISHU_APP_ID', 'FEISHU_APP_SECRET', 'FEISHU_WIKI_URL', 'FEISHU_SPACE_ID']) {
  if (!source[key]?.trim()) {
    throw new Error('KNOWLEDGE_SOURCE_CONFIG_REQUIRED');
  }
}
const app = dotenv.parse(fs.readFileSync(`${root}/app.env`));
if (!app.BOYANKB_KNOWLEDGE_AGENT_ID || !app.JWT_SECRET) {
  throw new Error('KNOWLEDGE_AGENT_CONFIG_REQUIRED');
}
const filename = `${root}/librechat.yaml`;
const original = fs.readFileSync(filename, 'utf8');
const config = yaml.load(original);
config.knowledge = {
  ...config.knowledge,
  enabled: true,
  sync: {
    ...config.knowledge?.sync,
    enabled: true,
    wikiUrl: '${FEISHU_WIKI_URL}',
    spaceId: '${FEISHU_SPACE_ID}',
  },
};
if (!fs.existsSync(`${filename}.before-sync`)) {
  fs.writeFileSync(`${filename}.before-sync`, original, { mode: 0o600, flag: 'wx' });
}
if (!fs.existsSync(`${root}/rag.env`)) {
  const template = fs.readFileSync('deploy/boyankb/rag.env.example', 'utf8');
  const content = template
    .replace(/^POSTGRES_PASSWORD=.*$/m, `POSTGRES_PASSWORD=${crypto.randomBytes(32).toString('hex')}`)
    .replace(/^JWT_SECRET=.*$/m, `JWT_SECRET=${app.JWT_SECRET}`);
  fs.writeFileSync(`${root}/rag.env`, content, { mode: 0o600, flag: 'wx' });
}
fs.writeFileSync(filename, yaml.dump(config, { lineWidth: -1 }), { mode: 0o600 });
const composePath = `${root}/.env`;
const compose = fs.readFileSync(composePath, 'utf8').replace(/^BOYANKB_SYNC_ENABLED=.*\r?\n?/gm, '');
fs.writeFileSync(composePath, `${compose.trimEnd()}\nBOYANKB_SYNC_ENABLED=1\n`, { mode: 0o600 });
