import { KnowledgeStreamError } from './stream';

type Identity = { role?: string; tenantId?: string };
type Agent = { _id: { toString(): string } };

export type KnowledgeStreamAccessDependencies = {
  userId: string;
  tenantId?: string;
  agentId: string;
  getUser: (userId: string) => Promise<Identity | null>;
  getAgent: (agentId: string) => Promise<Agent | null>;
  getBans: (userId: string) => Promise<unknown[]>;
  hasUserPermission: (userId: string, resourceId: string) => Promise<boolean>;
  now?: () => number;
};

export function createKnowledgeStreamAccessCheck(
  dependencies: KnowledgeStreamAccessDependencies,
): () => Promise<void> {
  return async (): Promise<void> => {
    if (!dependencies.userId || !dependencies.agentId) {
      throw new KnowledgeStreamError('KNOWLEDGE_ACCESS_DENIED');
    }
    const [user, agent, bans] = await Promise.all([
      dependencies.getUser(dependencies.userId),
      dependencies.getAgent(dependencies.agentId),
      dependencies.getBans(dependencies.userId),
    ]);
    if (!user || (user.tenantId ?? '') !== (dependencies.tenantId ?? '')) {
      throw new KnowledgeStreamError('KNOWLEDGE_ACCESS_DENIED');
    }
    if (!agent) {
      throw new KnowledgeStreamError('KNOWLEDGE_SOURCE_UNAVAILABLE');
    }
    const now = dependencies.now?.() ?? Date.now();
    if (
      bans.some((ban) => {
        if (!ban) {
          return false;
        }
        const expiresAt = Number((ban as { expiresAt?: unknown }).expiresAt);
        return (
          !(ban as { expiresAt?: unknown }).expiresAt || Number.isNaN(expiresAt) || expiresAt > now
        );
      })
    ) {
      throw new KnowledgeStreamError('KNOWLEDGE_USER_BANNED');
    }
    if (
      user.role !== 'ADMIN' &&
      !(await dependencies.hasUserPermission(dependencies.userId, agent._id.toString()))
    ) {
      throw new KnowledgeStreamError('KNOWLEDGE_ACCESS_DENIED');
    }
  };
}
