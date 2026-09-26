import type { RequestHandler } from 'express';
import { KnowledgeError } from './store';

type FileIdentity = { file_id?: string; context?: string };

export function createNativeKnowledgeAccess(deps: {
  activeFileIds: () => Promise<string[]>;
  getFile: (id: string) => Promise<FileIdentity | null>;
}): {
  filter: <T extends FileIdentity>(files: T[]) => Promise<T[]>;
  guard: RequestHandler;
} {
  async function filter<T extends FileIdentity>(files: T[]): Promise<T[]> {
    if (!files.some((file) => file.context === 'knowledge')) {
      return files;
    }
    try {
      const active = new Set(await deps.activeFileIds());
      return files.filter((file) => file.context !== 'knowledge' || active.has(file.file_id ?? ''));
    } catch {
      throw new KnowledgeError('KNOWLEDGE_FILE_UNAVAILABLE', 503);
    }
  }

  const guard: RequestHandler = async (req, res, next) => {
    if (!res.locals.knowledgeEnabled) {
      next();
      return;
    }
    try {
      const path = req.path.replace(/\/+$/, '');
      const match = /^\/(?:download(?:-url)?\/[^/]+\/([^/]+)|([^/]+)\/preview)$/i.exec(path);
      if (!match) {
        next();
        return;
      }
      const fileId = decodeURIComponent(match[1] ?? match[2]);
      const file = await deps.getFile(fileId);
      if (file?.context === 'knowledge' && !(await deps.activeFileIds()).includes(fileId)) {
        res.status(404).json({ code: 'KNOWLEDGE_FILE_UNAVAILABLE' });
        return;
      }
      next();
    } catch {
      res.status(404).json({ code: 'KNOWLEDGE_FILE_UNAVAILABLE' });
    }
  };

  return { filter, guard };
}
