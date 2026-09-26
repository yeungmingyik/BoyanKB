export type KnowledgeStreamStopCode =
  | 'KNOWLEDGE_ACCESS_DENIED'
  | 'KNOWLEDGE_USER_BANNED'
  | 'KNOWLEDGE_SOURCE_CHANGED'
  | 'KNOWLEDGE_SOURCE_UNAVAILABLE'
  | 'KNOWLEDGE_STREAM_UNAVAILABLE'
  | 'KNOWLEDGE_STREAM_CLOSED';

export class KnowledgeStreamError extends Error {
  readonly status: number;

  constructor(readonly code: KnowledgeStreamStopCode) {
    super(code);
    this.name = 'KnowledgeStreamError';
    this.status =
      code === 'KNOWLEDGE_ACCESS_DENIED' || code === 'KNOWLEDGE_USER_BANNED' ? 403 : 503;
  }
}

export type KnowledgeStreamGuard = {
  readonly signal: AbortSignal;
  readonly reason: KnowledgeStreamError | undefined;
  start(): Promise<void>;
  checkNow(): Promise<void>;
  allowWrite(): boolean;
  stop(code: KnowledgeStreamStopCode): void;
  dispose(): void;
};

export type KnowledgeStreamGuardDependencies = {
  checkAccess: (signal: AbortSignal) => Promise<void>;
  assertKnowledgeCurrent: (signal: AbortSignal) => Promise<void>;
  abort: (error: KnowledgeStreamError) => void | Promise<void>;
  onStop?: (error: KnowledgeStreamError) => void | Promise<void>;
  now?: () => number;
};

export function createKnowledgeStreamGuard(
  dependencies: KnowledgeStreamGuardDependencies,
): KnowledgeStreamGuard {
  const controller = new AbortController();
  const now = dependencies.now ?? (() => performance.now());
  let reason: KnowledgeStreamError | undefined;
  let disposed = false;
  let verifiedAt: number | undefined;
  let pending: Promise<void> | undefined;
  let interval: ReturnType<typeof setInterval> | undefined;
  let activeCheck: AbortController | undefined;

  function invoke(callback: ((error: KnowledgeStreamError) => void | Promise<void>) | undefined) {
    if (!callback || !reason) {
      return;
    }
    try {
      void Promise.resolve(callback(reason)).catch(() => {});
    } catch {
      return;
    }
  }

  function stop(code: KnowledgeStreamStopCode): void {
    if (reason || disposed) {
      return;
    }
    reason = new KnowledgeStreamError(code);
    clearInterval(interval);
    activeCheck?.abort();
    controller.abort(reason);
    invoke(dependencies.abort);
    invoke(dependencies.onStop);
  }

  function assertRunning(): void {
    if (reason || disposed) {
      throw reason ?? new KnowledgeStreamError('KNOWLEDGE_STREAM_CLOSED');
    }
  }

  function allowWrite(): boolean {
    if (reason || disposed || verifiedAt === undefined) {
      return false;
    }
    if (now() - verifiedAt >= 3000) {
      stop('KNOWLEDGE_STREAM_UNAVAILABLE');
      return false;
    }
    return true;
  }

  function refresh(): Promise<void> {
    assertRunning();
    if (pending) {
      return pending;
    }
    const startedAt = now();
    const check = new AbortController();
    activeCheck = check;
    let timeout: ReturnType<typeof setTimeout>;
    const operation = Promise.race([
      Promise.resolve().then(() =>
        Promise.all([
          dependencies.checkAccess(check.signal),
          dependencies.assertKnowledgeCurrent(check.signal),
        ]),
      ),
      new Promise<never>((_, reject) => {
        timeout = setTimeout(() => {
          reject(new KnowledgeStreamError('KNOWLEDGE_STREAM_UNAVAILABLE'));
          check.abort();
        }, 1500);
        timeout.unref?.();
      }),
    ])
      .then(() => {
        assertRunning();
        verifiedAt = startedAt;
        if (!allowWrite()) {
          throw reason ?? new KnowledgeStreamError('KNOWLEDGE_STREAM_UNAVAILABLE');
        }
      })
      .catch((error: unknown) => {
        stop(error instanceof KnowledgeStreamError ? error.code : 'KNOWLEDGE_STREAM_UNAVAILABLE');
        throw reason ?? new KnowledgeStreamError('KNOWLEDGE_STREAM_CLOSED');
      })
      .finally(() => {
        clearTimeout(timeout);
        if (activeCheck === check) {
          activeCheck = undefined;
        }
        if (pending === operation) {
          pending = undefined;
        }
      });
    pending = operation;
    return operation;
  }

  async function checkNow(): Promise<void> {
    assertRunning();
    if (pending) {
      await pending;
    }
    await refresh();
  }

  async function start(): Promise<void> {
    await checkNow();
    if (interval) {
      return;
    }
    interval = setInterval(() => {
      if (!allowWrite()) {
        return;
      }
      void refresh().catch(() => {});
    }, 1000);
    interval.unref?.();
  }

  function dispose(): void {
    disposed = true;
    clearInterval(interval);
    activeCheck?.abort();
  }

  return {
    signal: controller.signal,
    get reason(): KnowledgeStreamError | undefined {
      return reason;
    },
    start,
    checkNow,
    allowWrite,
    stop,
    dispose,
  };
}
