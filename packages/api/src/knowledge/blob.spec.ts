import os from 'node:os';
import path from 'node:path';
import { promises as fs } from 'node:fs';
import { LocalKnowledgeBlobStore } from './blob';

describe('immutable knowledge blobs', () => {
  let root: string;
  let store: LocalKnowledgeBlobStore;

  beforeEach(async () => {
    root = await fs.mkdtemp(path.join(os.tmpdir(), 'boyankb-blobs-'));
    store = new LocalKnowledgeBlobStore(root);
  });

  afterEach(async () => {
    await fs.rm(root, { recursive: true, force: true });
  });

  it('deduplicates concurrent immutable writes and checks content integrity', async () => {
    const results = await Promise.all(
      Array.from({ length: 8 }, () => store.put(Buffer.from('synthetic bytes'))),
    );
    expect(new Set(results.map((result) => result.key)).size).toBe(1);
    expect((await store.read(results[0].key)).toString()).toBe('synthetic bytes');
    expect(await fs.readdir(path.dirname(path.join(root, results[0].key)))).toHaveLength(1);
    await fs.writeFile(path.join(root, results[0].key), 'corrupt');
    await expect(store.read(results[0].key)).rejects.toThrow('KNOWLEDGE_BLOB_CORRUPT');
  });

  it.each(['../../secret', '/etc/passwd', 'C:\\secret', `sha256/ff/${'0'.repeat(64)}`])(
    'rejects unsafe keys: %s',
    async (key) => {
      await expect(store.read(key)).rejects.toThrow('KNOWLEDGE_BLOB_KEY_INVALID');
    },
  );
});
