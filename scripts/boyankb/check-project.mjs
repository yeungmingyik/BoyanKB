import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../', import.meta.url));
const read = (name) => readFileSync(path.join(root, name), 'utf8');
const version = read('VERSION').trim();
const upstream = JSON.parse(read('upstream.lock.json'));
const documents = [
  'README.md',
  'AGENTS.md',
  'Agent.md',
  'CLAUDE.md',
  'PRD.md',
  'CHANGELOG.md',
  'docs/boyankb/architecture.md',
  'docs/boyankb/sync.md',
  'docs/boyankb/feishu-setup.md',
  'docs/boyankb/source-coverage.md',
  'docs/boyankb/deployment.md',
  'docs/boyankb/models.md',
  'docs/boyankb/versions.md',
  'docs/boyankb/roadmap.md',
  'docs/boyankb/acceptance.md',
];

assert.match(version, /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(?:alpha|beta|rc)\.[1-9]\d*)?$/);
assert.match(upstream.commit, /^[a-f0-9]{40}$/);
assert.equal(upstream.branch, 'main');
assert.equal(upstream.repository, 'https://github.com/danny-avila/LibreChat.git');
assert.equal(upstream.license, 'MIT');
assert.equal(upstream.packageVersion, JSON.parse(read('package.json')).version);
assert.ok(read('README.md').includes(version));
assert.ok(read('CHANGELOG.md').includes(`## [${version}]`));
assert.ok(read('LICENSE').includes('Permission is hereby granted'));

for (const document of documents) {
  const content = read(document);
  assert.ok(content.trim(), `${document}: empty`);
  assert.ok(!content.includes('Co-authored-by:'), `${document}: attribution`);
  assert.ok(!/https?:\/\/[^/\s]+\.feishu\.cn\/(?:wiki|docx)\/[a-zA-Z0-9]+/.test(content), `${document}: source identifier`);
  for (const match of content.matchAll(/\[[^\]]*\]\(([^)]+)\)/g)) {
    const target = match[1].split('#')[0];
    if (!target || /^[a-z]+:/i.test(target)) {
      continue;
    }
    const resolved = path.resolve(root, path.dirname(document), decodeURIComponent(target));
    assert.ok(existsSync(resolved), `${document}: ${target}`);
  }
}

console.log(`BoyanKB ${version}: ${documents.length} documents verified`);
