import { SystemRoles } from 'librechat-data-provider';
import type { TStartupConfig, TUser } from 'librechat-data-provider';

export function isKnowledgeRestricted(
  startupConfig?: Pick<TStartupConfig, 'knowledge'>,
  user?: Pick<TUser, 'role'>,
): boolean {
  return startupConfig?.knowledge?.enabled === true && user?.role !== SystemRoles.ADMIN;
}
