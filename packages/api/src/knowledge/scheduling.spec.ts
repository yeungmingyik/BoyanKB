import { CallbackManager } from '@langchain/core/callbacks/manager';
import { createKnowledgeStreamCallback } from './scheduling';

const model = { lc: 1, type: 'not_implemented' as const, id: ['fixture'] };

describe('knowledge stream scheduling', () => {
  it('waits for the event loop before the native model start completes', async () => {
    const events: string[] = [];
    const manager = new CallbackManager();
    manager.addHandler(createKnowledgeStreamCallback());
    const task = new Promise<void>((resolve) => {
      setImmediate(() => {
        events.push('task');
        resolve();
      });
    });

    const start = manager.handleChatModelStart(model, [[]]).then(() => {
      events.push('model');
    });

    await Promise.all([task, start]);
    expect(events).toEqual(['task', 'model']);
  });

  it('runs queued event loop tasks before continuing each token in order', async () => {
    const events: string[] = [];
    const manager = new CallbackManager();
    manager.addHandler(createKnowledgeStreamCallback());
    const [run] = await manager.handleChatModelStart(model, [[]]);
    const tokens = ['课程', '共', '12', '次', '。[1]'];

    for (const token of tokens) {
      const task = new Promise<void>((resolve) => {
        setImmediate(() => {
          events.push('check');
          resolve();
        });
      });
      await run.handleLLMNewToken(token);
      events.push(token);
      await task;
    }

    expect(events).toEqual(tokens.flatMap((token) => ['check', token]));
    expect(events.filter((event) => event !== 'check').join('')).toBe(tokens.join(''));
  });

  it('keeps callback instances independent across model runs', () => {
    const first = createKnowledgeStreamCallback();
    const second = createKnowledgeStreamCallback();

    expect(first).not.toBe(second);
    expect(first.copy()).toMatchObject({ awaitHandlers: true, raiseError: true });
    expect(first.handleLLMEnd).toBeUndefined();
  });

  it('propagates scheduling failures through the native callback manager', async () => {
    const manager = new CallbackManager();
    manager.addHandler(createKnowledgeStreamCallback());
    const [run] = await manager.handleChatModelStart(model, [[]]);
    const failure = new Error('SYNTHETIC_SCHEDULING_FAILURE');
    const timers =
      jest.requireActual<typeof import('node:timers/promises')>('node:timers/promises');
    jest.spyOn(timers, 'setImmediate').mockRejectedValueOnce(failure);
    jest.spyOn(console, 'error').mockImplementation(() => undefined);

    await expect(run.handleLLMNewToken('fixture')).rejects.toBe(failure);
  });
});
