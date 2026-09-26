import { Types } from 'mongoose';
import { runAsSystem, tenantStorage } from '@librechat/data-schemas';
import {
  AccessRoleIds,
  Constants,
  extractVariableName,
  knowledgeConfigSchema,
  PermissionBits,
  PermissionTypes,
  Permissions,
  PrincipalType,
  ResourceType,
  SystemRoles,
} from 'librechat-data-provider';
import type { Request, Response, RequestHandler } from 'express';
import type { TKnowledgeConfig } from 'librechat-data-provider';

type KnowledgeAgent = {
  _id: { toString(): string };
  id: string;
  tools?: string[];
  model_parameters?: Record<string, unknown>;
  agent_ids?: string[];
  edges?: unknown[];
  skills_enabled?: boolean;
  actions?: string[];
  mcpServerNames?: string[];
  subagents?: { enabled?: boolean; agent_ids?: string[]; graphs?: unknown[] };
};

type KnowledgePrincipal = {
  type?: string;
  id?: string | null;
  accessRoleId?: string;
};

type KnowledgeUser = {
  id?: string;
  _id?: { toString(): string };
  role?: string;
  tenantId?: string;
};

export type KnowledgeAccessDependencies = {
  getConfig: () => Promise<TKnowledgeConfig | undefined>;
  getAgent: (query: { id: string }) => Promise<KnowledgeAgent | null>;
  hasUserPermission: (userId: string, resourceId: string) => Promise<boolean>;
  deleteUserSessions: (userId: string) => Promise<unknown>;
};

type PermissionUpdate = {
  resourceType: string;
  resourceId: string;
  updated?: KnowledgePrincipal[];
  public?: boolean;
};

type PermissionRevocation = {
  resourceType: string;
  resourceId: string;
  revoked: KnowledgePrincipal[];
};

export type KnowledgeAccessService = {
  authorize: RequestHandler;
  createBoundary: (authenticate: RequestHandler, checkBan: RequestHandler) => RequestHandler;
  validatePermissionUpdate: (params: PermissionUpdate) => Promise<string | undefined>;
  revokeSessions: (params: PermissionRevocation) => Promise<void>;
};

const publicPaths = new Set([
  '/api/config',
  '/api/auth/login',
  '/api/auth/logout',
  '/api/auth/refresh',
  '/api/auth/register',
  '/api/auth/requestpasswordreset',
  '/api/auth/resetpassword',
  '/api/auth/2fa/verify-temp',
  '/api/user/verify',
  '/api/user/verify/resend',
]);

const accountPaths = new Set([
  '/api/user',
  '/api/user/terms',
  '/api/user/terms/accept',
  '/api/user/preferences',
  '/api/auth/2fa/enable',
  '/api/auth/2fa/verify',
  '/api/auth/2fa/confirm',
  '/api/auth/2fa/disable',
  '/api/auth/2fa/backup/regenerate',
]);

export function resolveKnowledgeConfig(
  config: TKnowledgeConfig | undefined,
  environment: Record<string, string | undefined> = process.env,
): TKnowledgeConfig | undefined {
  if (!config) {
    return;
  }
  const variable = config.agentId ? extractVariableName(config.agentId) : undefined;
  return knowledgeConfigSchema.parse({
    ...config,
    agentId: variable ? (environment[variable] ?? '') : config.agentId,
    ...(config.sync
      ? {
          sync: {
            ...config.sync,
            wikiUrl: resolveValue(config.sync.wikiUrl, environment),
            spaceId: resolveValue(config.sync.spaceId, environment),
          },
        }
      : {}),
  });
}

function resolveValue(value: string, environment: Record<string, string | undefined>): string {
  const variable = extractVariableName(value);
  return variable ? (environment[variable] ?? '') : value;
}

export function publicKnowledgeConfig(
  config: TKnowledgeConfig | undefined,
): Pick<TKnowledgeConfig, 'enabled' | 'agentId'> | undefined {
  if (!config) {
    return;
  }
  return { enabled: config.enabled, agentId: config.agentId };
}

function requestPath(req: Request): string {
  return (req.baseUrl + req.path).replace(/\/+$/, '').toLowerCase();
}

function deny(res: Response, code: string, status = 403): void {
  res.status(status).json({ code });
}

function emptySelection(value: unknown): boolean {
  return value == null || (Array.isArray(value) && value.length === 0);
}

function allowedUserRequest(req: Request, agentId: string): boolean {
  const path = requestPath(req);
  const read = req.method === 'GET' || req.method === 'HEAD';
  if (
    read &&
    /^\/api\/knowledge\/(?:tree|documents\/[^/]+(?:\/revisions\/[^/]+)?|assets\/[^/]+)$/.test(path)
  ) {
    return true;
  }
  if (read && path.startsWith('/images/')) {
    return true;
  }
  if (read && /^\/api\/(convos|messages|search|tags)(\/|$)/.test(path)) {
    return !path.startsWith('/api/convos/gen_title/');
  }
  if (read && /^\/api\/(endpoints|models|balance|roles|banner)(\/|$)/.test(path)) {
    return true;
  }
  if (read && /^\/api\/user\/settings\/(favorites|pinned-order)$/.test(path)) {
    return true;
  }
  if (/^\/api\/keys(?:\/[^/]+)?$/.test(path) && ['GET', 'PUT', 'DELETE'].includes(req.method)) {
    return true;
  }
  if (
    req.method === 'POST' &&
    /^\/api\/convos\/(update|archive|archive\/all|pin|fork|duplicate)$/.test(path)
  ) {
    return true;
  }
  if (req.method === 'DELETE' && /^\/api\/convos(\/all)?$/.test(path)) {
    return true;
  }
  if (
    (req.method === 'PUT' || req.method === 'DELETE') &&
    /^\/api\/messages\/[^/]+\/[^/]+(?:\/feedback)?$/.test(path)
  ) {
    return true;
  }
  if (read && path === '/api/agents') {
    return true;
  }
  if (read && path === `/api/agents/${agentId.toLowerCase()}`) {
    return true;
  }
  if (read && /^\/api\/agents\/chat\/(active|status\/[^/]+|stream\/[^/]+)$/.test(path)) {
    return true;
  }
  if (req.method === 'POST' && path === '/api/agents/chat/abort') {
    return true;
  }
  if (
    req.method === 'POST' &&
    /^\/api\/agents\/chat(?:\/[^/]+)?$/.test(path) &&
    ![
      '/api/agents/chat/resume',
      '/api/agents/chat/steer',
      '/api/agents/chat/queued-turns',
    ].includes(path)
  ) {
    const body = req.body ?? {};
    const savedAgent = body.endpoint === 'agents';
    return (
      (savedAgent
        ? body.agent_id === agentId
        : !body.agent_id || body.agent_id === Constants.EPHEMERAL_AGENT_ID) &&
      typeof body.endpoint === 'string' &&
      body.endpoint.length > 0 &&
      emptySelection(body.files) &&
      emptySelection(body.attachments) &&
      emptySelection(body.file_ids) &&
      emptySelection(body.manualSkills) &&
      body.addedConvo == null &&
      !body.chatProjectId &&
      emptySelection(body.tools) &&
      !body.web_search &&
      !body.url_context &&
      !body.execute_code &&
      !body.file_search &&
      !body.assistant_id &&
      !body.tool_resources &&
      !body.instructions &&
      !body.agent_ids &&
      emptySelection(body.codeWorkspaces) &&
      (!body.ephemeralAgent ||
        Object.entries(body.ephemeralAgent).every(
          ([key, value]) => key === 'artifacts' || !value || emptySelection(value),
        ))
    );
  }
  if (read && (path === '/api/files' || path === '/api/files/config')) {
    return true;
  }
  if (read && path === `/api/files/agent/${agentId.toLowerCase()}`) {
    return true;
  }
  if (read && /^\/api\/files\/(?:[^/]+\/preview|download(?:-url)?\/[^/]+\/[^/]+)$/.test(path)) {
    return true;
  }
  if (read && /^\/api\/permissions\/agent\/(?:effective\/all|[^/]+\/effective)$/.test(path)) {
    return true;
  }
  return false;
}

function unsafeAgent(agent: KnowledgeAgent): boolean {
  return (
    (agent.tools?.some((tool) => tool !== 'file_search') ?? false) ||
    Boolean(agent.model_parameters?.url_context) ||
    Boolean(agent.agent_ids?.length) ||
    Boolean(agent.edges?.length) ||
    Boolean(agent.skills_enabled) ||
    Boolean(agent.actions?.length) ||
    Boolean(agent.mcpServerNames?.length) ||
    Boolean(agent.subagents?.enabled) ||
    Boolean(agent.subagents?.agent_ids?.length) ||
    Boolean(agent.subagents?.graphs?.length)
  );
}

export function createKnowledgeAccess(deps: KnowledgeAccessDependencies): KnowledgeAccessService {
  async function getAccess(userId: string, config: TKnowledgeConfig) {
    if (!config.agentId) {
      return { access: false, configured: false };
    }
    const agent = await deps.getAgent({ id: config.agentId });
    if (!agent) {
      return { access: false, configured: false, agentId: config.agentId };
    }
    const access = await deps.hasUserPermission(userId, agent._id.toString());
    return { access, configured: true, agentId: config.agentId, agent };
  }

  const authorize: RequestHandler = async (req, res, next) => {
    try {
      const config = await deps.getConfig();
      if (!config?.enabled) {
        next();
        return;
      }
      res.locals.knowledgeEnabled = true;
      const user = req.user as KnowledgeUser | undefined;
      const userId = user?.id ?? user?._id?.toString();
      if (!userId) {
        deny(res, 'AUTH_REQUIRED', 401);
        return;
      }
      res.setHeader('Cache-Control', 'private, no-store');
      const path = requestPath(req);
      if (path === '/api/share' || path.startsWith('/api/share/')) {
        deny(res, 'KNOWLEDGE_SHARING_DISABLED');
        return;
      }
      const isAdmin = user?.role === SystemRoles.ADMIN;
      if (req.method === 'GET' && path === '/api/knowledge/access') {
        if (isAdmin) {
          const agent = config.agentId ? await deps.getAgent({ id: config.agentId }) : null;
          res.json({ access: true, configured: Boolean(agent), agentId: config.agentId });
          return;
        }
        const { access, configured, agentId } = await getAccess(userId, config);
        res.json({ access, configured, agentId });
        return;
      }
      if (isAdmin || accountPaths.has(path)) {
        next();
        return;
      }
      const access = await getAccess(userId, config);
      if (!access.configured) {
        deny(res, 'KNOWLEDGE_NOT_CONFIGURED', 503);
        return;
      }
      if (!access.access) {
        deny(res, 'KNOWLEDGE_ACCESS_DENIED');
        return;
      }
      if (!allowedUserRequest(req, config.agentId!)) {
        deny(res, 'KNOWLEDGE_OPERATION_DENIED');
        return;
      }
      if (
        req.method === 'POST' &&
        path.startsWith('/api/agents/chat') &&
        path !== '/api/agents/chat/abort' &&
        req.body?.endpoint === 'agents' &&
        access.agent &&
        unsafeAgent(access.agent)
      ) {
        deny(res, 'KNOWLEDGE_AGENT_UNSAFE', 503);
        return;
      }
      res.locals.knowledgeAgentFilter = { id: config.agentId };
      next();
    } catch (error) {
      next(error);
    }
  };

  function createBoundary(authenticate: RequestHandler, checkBan: RequestHandler): RequestHandler {
    return async (req, res, next) => {
      try {
        const config = await deps.getConfig();
        if (!config?.enabled || publicPaths.has(requestPath(req))) {
          next();
          return;
        }
        authenticate(req, res, (error) => {
          if (error) {
            next(error);
            return;
          }
          checkBan(req, res, (banError) => {
            if (banError) {
              next(banError);
              return;
            }
            authorize(req, res, next);
          });
        });
      } catch (error) {
        next(error);
      }
    };
  }

  async function validatePermissionUpdate(params: PermissionUpdate): Promise<string | undefined> {
    const config = await deps.getConfig();
    if (!config?.enabled || !config.agentId || params.resourceType !== ResourceType.AGENT) {
      return;
    }
    const agent = await deps.getAgent({ id: config.agentId });
    if (agent?._id.toString() !== params.resourceId.toLowerCase()) {
      return;
    }
    if (
      params.public === true ||
      (params.updated ?? []).some(
        (principal) =>
          principal.type !== PrincipalType.USER ||
          principal.accessRoleId !== AccessRoleIds.AGENT_VIEWER,
      )
    ) {
      return 'KNOWLEDGE_INDIVIDUAL_VIEW_REQUIRED';
    }
  }

  async function revokeSessions(params: PermissionRevocation): Promise<void> {
    const config = await deps.getConfig();
    if (!config?.enabled || !config.agentId || params.resourceType !== ResourceType.AGENT) {
      return;
    }
    const agent = await deps.getAgent({ id: config.agentId });
    if (agent?._id.toString() !== params.resourceId.toLowerCase()) {
      return;
    }
    const userIds = new Set(
      params.revoked
        .filter((principal) => principal.type === PrincipalType.USER && principal.id)
        .map((principal) => principal.id!),
    );
    await Promise.all([...userIds].map((userId) => deps.deleteUserSessions(userId)));
  }

  return { authorize, createBoundary, validatePermissionUpdate, revokeSessions };
}

export function createKnowledgeUserPermissionChecker(
  hasPermission: (
    principals: { principalType: string; principalId: Types.ObjectId }[],
    resourceType: string,
    resourceId: Types.ObjectId,
    permission: number,
  ) => Promise<boolean>,
): (userId: string, resourceId: string) => Promise<boolean> {
  return (userId: string, resourceId: string) =>
    hasPermission(
      [{ principalType: PrincipalType.USER, principalId: new Types.ObjectId(userId) }],
      ResourceType.AGENT,
      new Types.ObjectId(resourceId),
      PermissionBits.VIEW,
    );
}

export function getKnowledgeRolePermissions(
  role: string,
): Partial<Record<PermissionTypes, Record<string, boolean>>> {
  const admin = role === SystemRoles.ADMIN;
  return {
    [PermissionTypes.AGENTS]: {
      [Permissions.USE]: true,
      [Permissions.CREATE]: admin,
      [Permissions.SHARE]: admin,
      [Permissions.SHARE_PUBLIC]: false,
    },
    [PermissionTypes.PEOPLE_PICKER]: {
      [Permissions.VIEW_USERS]: admin,
      [Permissions.VIEW_GROUPS]: false,
      [Permissions.VIEW_ROLES]: false,
    },
    [PermissionTypes.SHARED_LINKS]: {
      [Permissions.USE]: false,
      [Permissions.CREATE]: false,
      [Permissions.SHARE]: false,
      [Permissions.SHARE_PUBLIC]: false,
    },
    ...(!admin
      ? {
          [PermissionTypes.RUN_CODE]: { [Permissions.USE]: false },
          [PermissionTypes.WEB_SEARCH]: { [Permissions.USE]: false },
          [PermissionTypes.MULTI_CONVO]: { [Permissions.USE]: false },
          [PermissionTypes.MCP_SERVERS]: { [Permissions.USE]: false, [Permissions.CREATE]: false },
          [PermissionTypes.SKILLS]: { [Permissions.USE]: false, [Permissions.CREATE]: false },
        }
      : {}),
  };
}

export function createKnowledgeImageAuthorizer({
  getUser,
  checkBan,
  authorize,
}: {
  getUser: (userId: string) => Promise<KnowledgeUser | null>;
  checkBan: RequestHandler;
  authorize: RequestHandler;
}): (userId: string, req: Request, res: Response) => Promise<boolean> {
  return async (userId: string, req: Request, res: Response): Promise<boolean> => {
    const user = await getUser(userId);
    if (!user) {
      deny(res, 'AUTH_REQUIRED', 401);
      return false;
    }
    req.user = user;
    user.id = userId;
    const authorizeViewer = async () => {
      let allowed = false;
      await checkBan(req, res, (error) => {
        if (error) {
          throw error;
        }
        allowed = true;
      });
      if (!allowed) {
        return false;
      }
      allowed = false;
      await authorize(req, res, (error) => {
        if (error) {
          throw error;
        }
        allowed = true;
      });
      return allowed;
    };
    return user.tenantId
      ? tenantStorage.run({ tenantId: user.tenantId, userId }, authorizeViewer)
      : runAsSystem(authorizeViewer);
  };
}
