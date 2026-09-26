import path from 'node:path';
import { promises as fs } from 'node:fs';
import { createHash, randomUUID } from 'node:crypto';

export interface KnowledgeBlob {
  key: string;
  size: number;
  hash: string;
}

export interface KnowledgeBlobStore {
  put(bytes: Buffer): Promise<KnowledgeBlob>;
  read(key: string): Promise<Buffer>;
}

export class LocalKnowledgeBlobStore implements KnowledgeBlobStore {
  readonly root: string;

  constructor(root: string) {
    this.root = path.resolve(root);
  }

  private location(key: string): string {
    if (!/^sha256\/[a-f0-9]{2}\/[a-f0-9]{64}$/.test(key) || key.slice(7, 9) !== key.slice(10, 12)) {
      throw new Error('KNOWLEDGE_BLOB_KEY_INVALID');
    }
    return path.join(this.root, key);
  }

  async put(bytes: Buffer): Promise<KnowledgeBlob> {
    const hash = createHash('sha256').update(bytes).digest('hex');
    const key = `sha256/${hash.slice(0, 2)}/${hash}`;
    const destination = this.location(key);
    await fs.mkdir(path.dirname(destination), { recursive: true, mode: 0o700 });
    const temporary = `${destination}.${randomUUID()}.tmp`;
    try {
      const file = await fs.open(temporary, 'wx', 0o600);
      try {
        await file.writeFile(bytes);
        await file.sync();
      } finally {
        await file.close();
      }
      try {
        await fs.link(temporary, destination);
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'EEXIST') {
          throw error;
        }
        await this.read(key);
      }
    } finally {
      await fs.unlink(temporary).catch(() => undefined);
    }
    return { key, size: bytes.length, hash };
  }

  async read(key: string): Promise<Buffer> {
    const location = this.location(key);
    if (!(await fs.lstat(location)).isFile()) {
      throw new Error('KNOWLEDGE_BLOB_INVALID');
    }
    const bytes = await fs.readFile(location);
    if (createHash('sha256').update(bytes).digest('hex') !== key.slice(10)) {
      throw new Error('KNOWLEDGE_BLOB_CORRUPT');
    }
    return bytes;
  }
}
