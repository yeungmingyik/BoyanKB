import { setImmediate } from 'node:timers/promises';
import { BaseCallbackHandler } from '@langchain/core/callbacks/base';

class KnowledgeStreamCallback extends BaseCallbackHandler {
  name = 'boyankb-knowledge-stream-scheduling';
  awaitHandlers = true;
  raiseError = true;

  async handleChatModelStart(): Promise<void> {
    await setImmediate();
  }

  async handleLLMNewToken(): Promise<void> {
    await setImmediate();
  }
}

export function createKnowledgeStreamCallback(): BaseCallbackHandler {
  return new KnowledgeStreamCallback();
}
