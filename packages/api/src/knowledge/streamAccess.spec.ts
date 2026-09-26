import { createKnowledgeStreamAccessCheck } from './streamAccess';

function fixture() {
  const dependencies = {
    userId: 'synthetic-user',
    agentId: 'synthetic-agent',
    tenantId: 'synthetic-tenant',
    getUser: jest.fn(async () => ({ role: 'USER', tenantId: 'synthetic-tenant' })),
    getAgent: jest.fn(async () => ({ _id: { toString: () => 'synthetic-resource' } })),
    getBans: jest.fn<Promise<unknown[]>, [string]>().mockResolvedValue([]),
    hasUserPermission: jest.fn(async () => true),
    now: () => 10000,
  };
  return { ...dependencies, check: createKnowledgeStreamAccessCheck(dependencies) };
}

describe('knowledge stream fresh access', () => {
  it('queries the current user, individual VIEW grant and ban source on every check', async () => {
    const { check, getUser, getAgent, getBans, hasUserPermission } = fixture();
    await check();
    hasUserPermission.mockResolvedValue(false);
    await expect(check()).rejects.toMatchObject({ code: 'KNOWLEDGE_ACCESS_DENIED' });
    expect(getUser).toHaveBeenCalledTimes(2);
    expect(getAgent).toHaveBeenCalledTimes(2);
    expect(getBans).toHaveBeenCalledTimes(2);
    expect(hasUserPermission).toHaveBeenLastCalledWith('synthetic-user', 'synthetic-resource');
  });

  it('honors a new ban after an earlier uncached negative read', async () => {
    const { check, getBans } = fixture();
    await check();
    getBans.mockResolvedValue([{ expiresAt: 11000 }]);
    await expect(check()).rejects.toMatchObject({ code: 'KNOWLEDGE_USER_BANNED' });
  });

  it('allows expired bans and rejects malformed active ban records', async () => {
    const { check, getBans } = fixture();
    getBans.mockResolvedValue([{ expiresAt: 9999 }]);
    await check();
    getBans.mockResolvedValue([{ expiresAt: 'unreadable' }]);
    await expect(check()).rejects.toMatchObject({ code: 'KNOWLEDGE_USER_BANNED' });
  });

  it('checks bans for ADMIN while preserving the native VIEW bypass', async () => {
    const { check, getUser, getBans, hasUserPermission } = fixture();
    getUser.mockResolvedValue({ role: 'ADMIN', tenantId: 'synthetic-tenant' });
    hasUserPermission.mockResolvedValue(false);
    await check();
    expect(hasUserPermission).not.toHaveBeenCalled();
    getBans.mockResolvedValue([{ expiresAt: 11000 }]);
    await expect(check()).rejects.toMatchObject({ code: 'KNOWLEDGE_USER_BANNED' });
  });

  it('rechecks demotion and tenant changes instead of trusting the original JWT role', async () => {
    const { check, getUser, hasUserPermission } = fixture();
    getUser.mockResolvedValueOnce({ role: 'ADMIN', tenantId: 'synthetic-tenant' });
    await check();
    hasUserPermission.mockResolvedValue(false);
    await expect(check()).rejects.toMatchObject({ code: 'KNOWLEDGE_ACCESS_DENIED' });
    hasUserPermission.mockResolvedValue(true);
    getUser.mockResolvedValue({ role: 'USER', tenantId: 'different-tenant' });
    await expect(check()).rejects.toMatchObject({ code: 'KNOWLEDGE_ACCESS_DENIED' });
  });

  it('refuses deleted accounts and deleted knowledge agents', async () => {
    const { check, getUser, getAgent } = fixture();
    getUser.mockResolvedValueOnce(null!);
    await expect(check()).rejects.toMatchObject({ code: 'KNOWLEDGE_ACCESS_DENIED' });
    getAgent.mockResolvedValueOnce(null!);
    await expect(check()).rejects.toMatchObject({ code: 'KNOWLEDGE_SOURCE_UNAVAILABLE' });
  });
});
