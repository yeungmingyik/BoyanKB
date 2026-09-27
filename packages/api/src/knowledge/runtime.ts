import { getChatModelClass, Providers } from '@librechat/agents';
import type { TKnowledgeConfig } from 'librechat-data-provider';

let initialized = false;

export function initializeKnowledgeRuntime(config?: Pick<TKnowledgeConfig, 'enabled'>): void {
  if (config?.enabled !== true || initialized) {
    return;
  }
  try {
    for (const provider of Object.values(Providers)) {
      getChatModelClass(provider);
    }
    initialized = true;
  } catch {
    throw new Error('KNOWLEDGE_RUNTIME_UNAVAILABLE');
  }
}
