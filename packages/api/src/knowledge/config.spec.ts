import { knowledgeConfigSchema } from 'librechat-data-provider';
import { publicKnowledgeConfig, resolveKnowledgeConfig } from './access';

describe('knowledge configuration', () => {
  it('resolves private source variables without returning them to clients', () => {
    const config = knowledgeConfigSchema.parse({
      enabled: true,
      agentId: '${AGENT_ID}',
      sync: { enabled: true, wikiUrl: '${WIKI_URL}', spaceId: '${SPACE_ID}' },
    });
    const resolved = resolveKnowledgeConfig(config, {
      AGENT_ID: 'agent_test',
      WIKI_URL: 'https://example.feishu.cn/wiki/synthetic',
      SPACE_ID: '123',
    });
    expect(resolved?.sync?.wikiUrl).toBe('https://example.feishu.cn/wiki/synthetic');
    expect(resolved?.sync?.spaceId).toBe('123');
    expect(publicKnowledgeConfig(resolved)).toEqual({ enabled: true, agentId: 'agent_test' });
  });

  it.each([
    { pollIntervalMs: 0 },
    { maxRetries: 100 },
    { maxAssetBytes: -1 },
    { snapshotRetentionDays: 0 },
  ])('rejects unsafe synchronization limits %p', (sync) => {
    expect(knowledgeConfigSchema.safeParse({ sync }).success).toBe(false);
  });
});
