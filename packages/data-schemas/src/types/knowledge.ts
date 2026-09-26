export type KnowledgeDocumentState =
  | 'pending'
  | 'extracting'
  | 'indexing'
  | 'published'
  | 'partial'
  | 'failed'
  | 'unsupported'
  | 'inaccessible'
  | 'removed';

export interface KnowledgeSourceRecord {
  id: string;
  spaceId: string;
  agentId: string;
  enabled: boolean;
  health: 'pending' | 'healthy' | 'paused' | 'disabled';
  errorCode?: string;
  leaseOwner?: string;
  leaseFence: number;
  leaseUntil: Date;
  writeSequence: number;
  accessEpoch: number;
  activeRunId?: string;
  lastCompleteScan?: Date;
  lastPollAt?: Date;
  lastReconcileAt?: Date;
  createdAt?: Date;
  updatedAt?: Date;
}

export interface KnowledgeNodeRecord {
  id: string;
  sourceId: string;
  nodeToken: string;
  parentId?: string;
  documentId: string;
  title: string;
  objType: string;
  hasChildren: boolean;
  lastSeenRunId: string;
  accessEpoch: number;
  state: 'active' | 'missing' | 'removed' | 'inaccessible' | 'out_of_scope';
  missingRunId?: string;
  createdAt?: Date;
  updatedAt?: Date;
}

export interface KnowledgeDocumentRecord {
  id: string;
  sourceId: string;
  objType: string;
  objToken: string;
  title: string;
  status: KnowledgeDocumentState;
  activeRevisionId?: string;
  accessEpoch: number;
  requiresRevalidation: boolean;
  lastAttemptRunId?: string;
  errorCode?: string;
  sourceUpdatedAt?: Date;
  publishedAt?: Date;
  createdAt?: Date;
  updatedAt?: Date;
}

export interface KnowledgeRevisionRecord {
  id: string;
  sourceId: string;
  documentId: string;
  idempotencyKey: string;
  sourceRevision: string;
  sourceUpdatedAt?: Date;
  contentHash: string;
  blobKey: string;
  text: string;
  parserVersion: string;
  indexVersion: string;
  nativeFileIds: string[];
  assetIds: string[];
  complete: boolean;
  missing: { blockId: string; blockType: number; reason: string }[];
  status: 'staged' | 'partial' | 'indexed' | 'published';
  extractedAt: Date;
  indexedAt?: Date;
  publishedAt?: Date;
  createdAt?: Date;
  updatedAt?: Date;
}

export interface KnowledgeAssetRecord {
  id: string;
  sourceId: string;
  documentId: string;
  revisionId: string;
  mediaId: string;
  blobKey: string;
  contentType: string;
  name: string;
  size: number;
  createdAt?: Date;
  updatedAt?: Date;
}

export interface KnowledgeRunRecord {
  id: string;
  sourceId: string;
  idempotencyKey: string;
  actorId?: string;
  retryRunId?: string;
  mode: 'incremental' | 'full';
  status: 'queued' | 'running' | 'succeeded' | 'partial' | 'failed';
  phase: 'validate' | 'enumerate' | 'extract' | 'reconcile' | 'finish';
  enumerationComplete: boolean;
  reconcileCursor?: string;
  counts: Record<string, number>;
  errorCode?: string;
  startedAt?: Date;
  finishedAt?: Date;
  createdAt?: Date;
  updatedAt?: Date;
}

export interface KnowledgeItemRecord {
  id: string;
  sourceId: string;
  runId: string;
  kind: 'folder' | 'document' | 'confirmation';
  key: string;
  documentId?: string;
  nodeId?: string;
  parentToken?: string;
  cursor?: string;
  cursorHistory: string[];
  status: 'pending' | 'running' | 'succeeded' | 'partial' | 'failed';
  errorCode?: string;
  attempts: number;
  outcome?: string;
  revisionId?: string;
  missing?: { blockId: string; blockType: number; reason: string }[];
  retryAt?: Date;
  createdAt?: Date;
  updatedAt?: Date;
}

export interface KnowledgeAuditRecord {
  id: string;
  sourceId: string;
  actorId?: string;
  action: string;
  resourceId: string;
  result: string;
  at: Date;
}
