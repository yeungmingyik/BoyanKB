import express from 'express';
import type {
  KnowledgeTreeResponse,
  KnowledgeDocumentResponse,
  KnowledgeSyncRunsResponse,
  KnowledgeSyncRunResponse,
  KnowledgeSyncRun,
} from 'librechat-data-provider';
import type { Request, RequestHandler, Response } from 'express';
import type { KnowledgeAssetContent, KnowledgePage } from './store';
import { KnowledgeError } from './store';

export interface KnowledgeReadService {
  listTree: (page: KnowledgePage & { parentId?: string }) => Promise<KnowledgeTreeResponse>;
  readDocument: (
    id: string,
    options?: { revisionId?: string },
  ) => Promise<KnowledgeDocumentResponse>;
  readAsset: (id: string) => Promise<KnowledgeAssetContent>;
  listRuns: (page: KnowledgePage) => Promise<KnowledgeSyncRunsResponse>;
  getRun: (id: string, page?: KnowledgePage) => Promise<KnowledgeSyncRunResponse>;
  enqueue: (input: {
    mode: 'full' | 'incremental';
    idempotencyKey: string;
    actorId?: string;
    retryRunId?: string;
  }) => Promise<KnowledgeSyncRun>;
  activeFileIds: () => Promise<string[]>;
}

const internalId = /^(?:[a-z]+_[a-f0-9]{64}|[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12})$/;

function user(req: Request): { id?: string; role?: string } {
  return (req.user ?? {}) as { id?: string; role?: string };
}

function id(value: unknown): string {
  if (typeof value !== 'string' || !internalId.test(value)) {
    throw new KnowledgeError('KNOWLEDGE_NOT_FOUND', 404);
  }
  return value;
}

function page(req: Request): KnowledgePage {
  const cursor = req.query.cursor === undefined ? undefined : id(req.query.cursor);
  const limit = req.query.limit === undefined ? 100 : Number(req.query.limit);
  if (!Number.isInteger(limit) || limit < 1 || limit > 200) {
    throw new KnowledgeError('KNOWLEDGE_QUERY_INVALID', 400);
  }
  return { cursor, limit };
}

function key(req: Request): string {
  const value = req.get('Idempotency-Key');
  if (!value || !/^[a-zA-Z0-9_-]{8,128}$/.test(value)) {
    throw new KnowledgeError('KNOWLEDGE_IDEMPOTENCY_KEY_REQUIRED', 400);
  }
  return value;
}

function fail(res: Response, error: unknown) {
  if (error instanceof KnowledgeError) {
    res.status(error.status).json({ code: error.code });
    return;
  }
  res.status(503).json({ code: 'KNOWLEDGE_UNAVAILABLE' });
}

function assetRange(header: string | undefined, size: number): [number, number] | undefined {
  if (!header) {
    return;
  }
  const match = /^bytes=(\d*)-(\d*)$/.exec(header);
  if (!match || (!match[1] && !match[2]) || size === 0) {
    throw new KnowledgeError('KNOWLEDGE_RANGE_INVALID', 416);
  }
  const start = match[1] ? Number(match[1]) : Math.max(0, size - Number(match[2]));
  const end = match[1] && match[2] ? Math.min(Number(match[2]), size - 1) : size - 1;
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start > end || start >= size) {
    throw new KnowledgeError('KNOWLEDGE_RANGE_INVALID', 416);
  }
  return [start, end];
}

export function createKnowledgeRouter(deps: {
  getService: () => Promise<KnowledgeReadService>;
  authorize: RequestHandler;
}): express.Router {
  const router = express.Router();
  router.use(deps.authorize);
  router.use((_req, res, next) => {
    res.setHeader('Cache-Control', 'private, no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    next();
  });

  const handle =
    (fn: (req: Request, res: Response, service: KnowledgeReadService) => Promise<void>) =>
    async (req: Request, res: Response) => {
      try {
        await fn(req, res, await deps.getService());
      } catch (error) {
        fail(res, error);
      }
    };

  const admin: RequestHandler = (req, res, next) => {
    if (user(req).role !== 'ADMIN') {
      res.status(403).json({ code: 'KNOWLEDGE_ADMIN_REQUIRED' });
      return;
    }
    next();
  };

  router.get(
    '/tree',
    handle(async (req, res, service) => {
      const parentId = req.query.parentId === undefined ? undefined : id(req.query.parentId);
      res.json(await service.listTree({ ...page(req), parentId }));
    }),
  );

  router.get(
    '/documents/:id',
    handle(async (req, res, service) => {
      res.json(await service.readDocument(id(req.params.id)));
    }),
  );

  router.get(
    '/documents/:id/revisions/:revisionId',
    handle(async (req, res, service) => {
      res.json(
        await service.readDocument(id(req.params.id), { revisionId: id(req.params.revisionId) }),
      );
    }),
  );

  router.get(
    '/assets/:id',
    handle(async (req, res, service) => {
      const asset = await service.readAsset(id(req.params.id));
      res.setHeader('Accept-Ranges', 'bytes');
      let range: [number, number] | undefined;
      try {
        range = assetRange(req.get('Range'), asset.size);
      } catch (error) {
        res.setHeader('Content-Range', `bytes */${asset.size}`);
        throw error;
      }
      const image = /^(?:image\/png|image\/jpeg|image\/gif|image\/webp)$/.test(asset.contentType);
      const name = encodeURIComponent(
        Array.from(asset.name)
          .filter((value) => value.charCodeAt(0) >= 32 && value.charCodeAt(0) !== 127)
          .slice(0, 180)
          .join(''),
      ).replace(/['()*]/g, (value) => `%${value.charCodeAt(0).toString(16).toUpperCase()}`);
      res.setHeader('Content-Type', image ? asset.contentType : 'application/octet-stream');
      res.setHeader(
        'Content-Disposition',
        `${image ? 'inline' : 'attachment'}; filename="document"; filename*=UTF-8''${name}`,
      );
      res.setHeader('Content-Security-Policy', "default-src 'none'; sandbox");
      const bytes = range ? asset.bytes.subarray(range[0], range[1] + 1) : asset.bytes;
      if (range) {
        res.status(206).setHeader('Content-Range', `bytes ${range[0]}-${range[1]}/${asset.size}`);
      }
      res.setHeader('Content-Length', bytes.length);
      res.end(req.method === 'HEAD' ? undefined : bytes);
    }),
  );

  router.get(
    '/sync-runs',
    admin,
    handle(async (req, res, service) => {
      res.json(await service.listRuns(page(req)));
    }),
  );

  router.get(
    '/sync-runs/:id',
    admin,
    handle(async (req, res, service) => {
      res.json(await service.getRun(id(req.params.id), page(req)));
    }),
  );

  router.post(
    '/sync-runs',
    admin,
    handle(async (req, res, service) => {
      if (!['full', 'incremental'].includes(req.body?.mode)) {
        throw new KnowledgeError('KNOWLEDGE_MODE_INVALID', 400);
      }
      res.status(202).json(
        await service.enqueue({
          mode: req.body.mode,
          idempotencyKey: key(req),
          actorId: user(req).id,
        }),
      );
    }),
  );

  router.post(
    '/sync-runs/:id/retry',
    admin,
    handle(async (req, res, service) => {
      res.status(202).json(
        await service.enqueue({
          mode: 'full',
          idempotencyKey: key(req),
          actorId: user(req).id,
          retryRunId: id(req.params.id),
        }),
      );
    }),
  );

  return router;
}
