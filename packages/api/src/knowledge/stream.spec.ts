import { createKnowledgeStreamGuard, KnowledgeStreamError } from './stream';

function fixture() {
  const dependencies = {
    checkAccess: jest.fn<Promise<void>, [AbortSignal]>(async () => {}),
    assertKnowledgeCurrent: jest.fn<Promise<void>, [AbortSignal]>(async () => {}),
    abort: jest.fn(),
    onStop: jest.fn(),
    now: () => Date.now(),
  };
  const guard = createKnowledgeStreamGuard(dependencies);
  return { guard, ...dependencies };
}

describe('knowledge stream guard', () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it('withholds output until the initial authorization and source checks complete', async () => {
    const { guard, checkAccess, assertKnowledgeCurrent } = fixture();
    expect(guard.allowWrite()).toBe(false);
    await guard.start();
    expect(checkAccess).toHaveBeenCalledTimes(1);
    expect(assertKnowledgeCurrent).toHaveBeenCalledTimes(1);
    expect(guard.allowWrite()).toBe(true);
    guard.dispose();
    expect(jest.getTimerCount()).toBe(0);
  });

  it.each(['KNOWLEDGE_ACCESS_DENIED', 'KNOWLEDGE_USER_BANNED'] as const)(
    'aborts within one second after %s and never reopens the stream',
    async (code) => {
      const { guard, checkAccess, abort, onStop } = fixture();
      await guard.start();
      checkAccess.mockRejectedValue(new KnowledgeStreamError(code));
      await jest.advanceTimersByTimeAsync(1000);
      expect(guard.allowWrite()).toBe(false);
      expect(guard.signal.aborted).toBe(true);
      expect(guard.reason?.code).toBe(code);
      expect(abort).toHaveBeenCalledTimes(1);
      expect(onStop).toHaveBeenCalledTimes(1);
      checkAccess.mockResolvedValue();
      await expect(guard.checkNow()).rejects.toMatchObject({ code });
      await jest.advanceTimersByTimeAsync(5000);
      expect(abort).toHaveBeenCalledTimes(1);
      expect(jest.getTimerCount()).toBe(0);
    },
  );

  it.each(['KNOWLEDGE_SOURCE_CHANGED', 'KNOWLEDGE_SOURCE_UNAVAILABLE'] as const)(
    'stops before another output after %s',
    async (code) => {
      const { guard, assertKnowledgeCurrent, abort } = fixture();
      await guard.start();
      assertKnowledgeCurrent.mockRejectedValue(new KnowledgeStreamError(code));
      await expect(guard.checkNow()).rejects.toMatchObject({ code });
      expect(guard.allowWrite()).toBe(false);
      expect(abort).toHaveBeenCalledTimes(1);
    },
  );

  it('fails closed when a fresh authorization read hangs', async () => {
    const { guard, checkAccess, abort } = fixture();
    await guard.start();
    checkAccess.mockImplementation(() => new Promise(() => {}));
    await jest.advanceTimersByTimeAsync(2500);
    expect(guard.reason?.code).toBe('KNOWLEDGE_STREAM_UNAVAILABLE');
    expect(guard.signal.aborted).toBe(true);
    expect(abort).toHaveBeenCalledTimes(1);
    expect(checkAccess.mock.calls[1][0].aborted).toBe(true);
    expect(guard.allowWrite()).toBe(false);
  });

  it('does not expose underlying exception details to a stream or abort callback', async () => {
    const { guard, checkAccess, abort } = fixture();
    checkAccess.mockRejectedValue(new Error('synthetic upstream secret and source body'));
    await expect(guard.start()).rejects.toMatchObject({ code: 'KNOWLEDGE_STREAM_UNAVAILABLE' });
    expect(JSON.stringify(abort.mock.calls)).not.toContain('synthetic');
    expect(guard.reason?.message).toBe('KNOWLEDGE_STREAM_UNAVAILABLE');
  });

  it('ignores a permission result arriving after its deadline', async () => {
    const { guard, checkAccess } = fixture();
    let resolve!: () => void;
    checkAccess.mockImplementation(() => new Promise<void>((done) => (resolve = done)));
    const start = guard.start().catch((error) => error);
    await jest.advanceTimersByTimeAsync(1500);
    expect(await start).toMatchObject({ code: 'KNOWLEDGE_STREAM_UNAVAILABLE' });
    resolve();
    await jest.advanceTimersByTimeAsync(0);
    expect(guard.allowWrite()).toBe(false);
    expect(jest.getTimerCount()).toBe(0);
  });

  it('checks authorization age synchronously even when scheduled polling has not run', async () => {
    let clock = 0;
    const abort = jest.fn();
    const guard = createKnowledgeStreamGuard({
      checkAccess: async () => {},
      assertKnowledgeCurrent: async () => {},
      abort,
      now: () => clock,
    });
    await guard.start();
    clock = 3000;
    expect(guard.allowWrite()).toBe(false);
    expect(abort).toHaveBeenCalledTimes(1);
  });

  it('forces another validation after an in-flight poll when the context changes', async () => {
    const { guard, assertKnowledgeCurrent } = fixture();
    await guard.start();
    let resolve!: () => void;
    assertKnowledgeCurrent.mockImplementationOnce(
      () => new Promise<void>((done) => (resolve = done)),
    );
    await jest.advanceTimersByTimeAsync(1000);
    const refresh = guard.checkNow().catch((error) => error);
    assertKnowledgeCurrent.mockRejectedValue(new KnowledgeStreamError('KNOWLEDGE_SOURCE_CHANGED'));
    resolve();
    expect(await refresh).toMatchObject({ code: 'KNOWLEDGE_SOURCE_CHANGED' });
    expect(assertKnowledgeCurrent).toHaveBeenCalledTimes(3);
    expect(guard.allowWrite()).toBe(false);
  });

  it('does not overlap polling reads', async () => {
    const { guard, checkAccess } = fixture();
    await guard.start();
    checkAccess.mockImplementationOnce(() => new Promise(() => {}));
    await jest.advanceTimersByTimeAsync(2000);
    expect(checkAccess).toHaveBeenCalledTimes(2);
    await jest.advanceTimersByTimeAsync(500);
    expect(guard.signal.aborted).toBe(true);
  });

  it('does not abort a completed generation while disposing its timer', async () => {
    const { guard, abort } = fixture();
    await guard.start();
    guard.dispose();
    await jest.advanceTimersByTimeAsync(5000);
    expect(abort).not.toHaveBeenCalled();
    expect(guard.allowWrite()).toBe(false);
    await expect(guard.checkNow()).rejects.toMatchObject({ code: 'KNOWLEDGE_STREAM_CLOSED' });
    expect(jest.getTimerCount()).toBe(0);
  });

  it('keeps output closed when abort or notification callbacks fail', async () => {
    const { guard, abort, onStop } = fixture();
    abort.mockImplementation(() => {
      throw new Error('synthetic secret');
    });
    onStop.mockRejectedValue(new Error('synthetic secret'));
    await guard.start();
    guard.stop('KNOWLEDGE_ACCESS_DENIED');
    await jest.advanceTimersByTimeAsync(0);
    expect(guard.allowWrite()).toBe(false);
    expect(guard.signal.aborted).toBe(true);
    expect(onStop).toHaveBeenCalledTimes(1);
  });
});
