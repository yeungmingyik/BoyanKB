export type TKnowledgeAccessResponse = {
  access: boolean;
  configured: boolean;
  agentId?: string;
};

export type KnowledgeReadingBlock = {
  id: string;
  type:
    | 'section'
    | 'paragraph'
    | 'heading'
    | 'bullet'
    | 'ordered'
    | 'callout'
    | 'quote'
    | 'table'
    | 'row'
    | 'cell'
    | 'image'
    | 'file'
    | 'unsupported';
  text: string;
  level?: number;
  children?: KnowledgeReadingBlock[];
  mediaId?: string;
};

export type KnowledgeDocumentStatus =
  | 'pending'
  | 'extracting'
  | 'indexing'
  | 'published'
  | 'partial'
  | 'failed'
  | 'unsupported'
  | 'inaccessible'
  | 'removed';

export type KnowledgeSourceStatus = 'pending' | 'ready' | 'paused' | 'error';

export type KnowledgeTreeNode = {
  id: string;
  parentId: string | null;
  documentId: string;
  title: string;
  objectType: string;
  status: KnowledgeDocumentStatus;
  readable: boolean;
  hasChildren: boolean;
  order: number;
};

export type KnowledgeTreeResponse = {
  items: KnowledgeTreeNode[];
  nextCursor?: string;
  sourceStatus: KnowledgeSourceStatus;
  lastCompleteScanAt?: string;
};

export type KnowledgeAsset = {
  id: string;
  mediaId: string;
  name: string;
  type: 'image' | 'file';
  mimeType: string;
  bytes: number;
};

export type KnowledgeDocumentResponse = {
  id: string;
  title: string;
  status: KnowledgeDocumentStatus;
  revision: {
    id: string;
    sourceUpdatedAt?: string;
    extractedAt: string;
    publishedAt: string;
  };
  blocks: KnowledgeReadingBlock[];
  assets: KnowledgeAsset[];
};

export type KnowledgeSyncCounts = {
  nodes: number;
  documents: number;
  published: number;
  unchanged: number;
  failed: number;
  unsupported: number;
  inaccessible: number;
  removed: number;
  media: number;
};

export type KnowledgeSyncItem = {
  id: string;
  documentId: string;
  title: string;
  status: KnowledgeDocumentStatus;
  errorCode?: string;
  missing?: { blockId: string; blockType: number; reason: string }[];
};

export type KnowledgeSyncRun = {
  id: string;
  mode: 'full' | 'incremental';
  status: 'queued' | 'running' | 'completed' | 'partial' | 'failed';
  phase: string;
  counts: KnowledgeSyncCounts;
  startedAt?: string;
  finishedAt?: string;
  errorCode?: string;
};

export type KnowledgeSyncRunsResponse = {
  items: KnowledgeSyncRun[];
  nextCursor?: string;
  sourceStatus: KnowledgeSourceStatus;
  lastCompleteScanAt?: string;
};

export type KnowledgeSyncRunResponse = KnowledgeSyncRun & {
  items: KnowledgeSyncItem[];
  nextCursor?: string;
};
