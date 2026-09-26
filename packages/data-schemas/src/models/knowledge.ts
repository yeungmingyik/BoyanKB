import { Schema } from 'mongoose';
import type { Model } from 'mongoose';
import type {
  KnowledgeSourceRecord,
  KnowledgeNodeRecord,
  KnowledgeDocumentRecord,
  KnowledgeRevisionRecord,
  KnowledgeAssetRecord,
  KnowledgeRunRecord,
  KnowledgeItemRecord,
  KnowledgeAuditRecord,
} from '~/types/knowledge';

const requiredString = { type: String, required: true };

const sourceSchema = new Schema<KnowledgeSourceRecord>(
  {
    id: requiredString,
    spaceId: { type: String, default: '' },
    agentId: { type: String, default: '' },
    enabled: { type: Boolean, default: true },
    health: {
      type: String,
      enum: ['pending', 'healthy', 'paused', 'disabled'],
      default: 'pending',
    },
    errorCode: String,
    leaseOwner: String,
    leaseFence: { type: Number, default: 0 },
    leaseUntil: { type: Date, default: () => new Date(0) },
    writeSequence: { type: Number, default: 0 },
    accessEpoch: { type: Number, default: 0 },
    activeRunId: String,
    lastCompleteScan: Date,
    lastPollAt: Date,
    lastReconcileAt: Date,
  },
  { timestamps: true },
);
sourceSchema.index({ id: 1 }, { unique: true });

const nodeSchema = new Schema<KnowledgeNodeRecord>(
  {
    id: requiredString,
    sourceId: requiredString,
    nodeToken: requiredString,
    parentId: String,
    documentId: requiredString,
    title: { type: String, default: '' },
    objType: requiredString,
    hasChildren: { type: Boolean, default: false },
    lastSeenRunId: requiredString,
    accessEpoch: { type: Number, default: 0 },
    state: {
      type: String,
      enum: ['active', 'missing', 'removed', 'inaccessible', 'out_of_scope'],
      default: 'active',
    },
    missingRunId: String,
  },
  { timestamps: true },
);
nodeSchema.index({ id: 1 }, { unique: true });
nodeSchema.index({ sourceId: 1, nodeToken: 1 }, { unique: true });
nodeSchema.index({ sourceId: 1, parentId: 1, id: 1 });
nodeSchema.index({ sourceId: 1, documentId: 1, state: 1 });
nodeSchema.index({ sourceId: 1, lastSeenRunId: 1 });

const documentSchema = new Schema<KnowledgeDocumentRecord>(
  {
    id: requiredString,
    sourceId: requiredString,
    objType: requiredString,
    objToken: requiredString,
    title: { type: String, default: '' },
    status: {
      type: String,
      enum: [
        'pending',
        'extracting',
        'indexing',
        'published',
        'partial',
        'failed',
        'unsupported',
        'inaccessible',
        'removed',
      ],
      default: 'pending',
    },
    activeRevisionId: String,
    accessEpoch: { type: Number, default: 0 },
    requiresRevalidation: { type: Boolean, default: false },
    lastAttemptRunId: String,
    errorCode: String,
    sourceUpdatedAt: Date,
    publishedAt: Date,
  },
  { timestamps: true },
);
documentSchema.index({ id: 1 }, { unique: true });
documentSchema.index({ sourceId: 1, objType: 1, objToken: 1 }, { unique: true });
documentSchema.index({ sourceId: 1, status: 1, id: 1 });

const revisionSchema = new Schema<KnowledgeRevisionRecord>(
  {
    id: requiredString,
    sourceId: requiredString,
    documentId: requiredString,
    idempotencyKey: requiredString,
    sourceRevision: requiredString,
    sourceUpdatedAt: Date,
    contentHash: requiredString,
    blobKey: requiredString,
    text: { type: String, default: '' },
    parserVersion: requiredString,
    indexVersion: requiredString,
    nativeFileIds: { type: [String], default: [] },
    assetIds: { type: [String], default: [] },
    complete: { type: Boolean, required: true },
    missing: {
      type: [{ blockId: String, blockType: Number, reason: String, _id: false }],
      default: [],
    },
    status: {
      type: String,
      enum: ['staged', 'partial', 'indexed', 'published'],
      default: 'staged',
    },
    extractedAt: { type: Date, required: true },
    indexedAt: Date,
    publishedAt: Date,
  },
  { timestamps: true },
);
revisionSchema.index({ id: 1 }, { unique: true });
revisionSchema.index({ sourceId: 1, idempotencyKey: 1 }, { unique: true });
revisionSchema.index({ sourceId: 1, documentId: 1, publishedAt: 1 });

const assetSchema = new Schema<KnowledgeAssetRecord>(
  {
    id: requiredString,
    sourceId: requiredString,
    documentId: requiredString,
    revisionId: requiredString,
    mediaId: requiredString,
    blobKey: requiredString,
    contentType: requiredString,
    name: requiredString,
    size: { type: Number, required: true, min: 0 },
  },
  { timestamps: true },
);
assetSchema.index({ id: 1 }, { unique: true });
assetSchema.index({ sourceId: 1, revisionId: 1, mediaId: 1 }, { unique: true });

const runSchema = new Schema<KnowledgeRunRecord>(
  {
    id: requiredString,
    sourceId: requiredString,
    idempotencyKey: requiredString,
    actorId: String,
    retryRunId: String,
    mode: { type: String, enum: ['incremental', 'full'], required: true },
    status: {
      type: String,
      enum: ['queued', 'running', 'succeeded', 'partial', 'failed'],
      default: 'queued',
    },
    phase: {
      type: String,
      enum: ['validate', 'enumerate', 'extract', 'reconcile', 'finish'],
      default: 'validate',
    },
    enumerationComplete: { type: Boolean, default: false },
    reconcileCursor: String,
    counts: { type: Schema.Types.Mixed, default: {} },
    errorCode: String,
    startedAt: Date,
    finishedAt: Date,
  },
  { timestamps: true },
);
runSchema.index({ id: 1 }, { unique: true });
runSchema.index({ sourceId: 1, idempotencyKey: 1 }, { unique: true });
runSchema.index({ sourceId: 1, status: 1, createdAt: 1 });

const itemSchema = new Schema<KnowledgeItemRecord>(
  {
    id: requiredString,
    sourceId: requiredString,
    runId: requiredString,
    kind: { type: String, enum: ['folder', 'document', 'confirmation'], required: true },
    key: requiredString,
    documentId: String,
    nodeId: String,
    parentToken: String,
    cursor: String,
    cursorHistory: { type: [String], default: [] },
    status: {
      type: String,
      enum: ['pending', 'running', 'succeeded', 'partial', 'failed'],
      default: 'pending',
    },
    errorCode: String,
    attempts: { type: Number, default: 0 },
    outcome: String,
    revisionId: String,
    missing: {
      type: [{ blockId: String, blockType: Number, reason: String, _id: false }],
      default: undefined,
    },
    retryAt: Date,
  },
  { timestamps: true },
);
itemSchema.index({ id: 1 }, { unique: true });
itemSchema.index({ runId: 1, kind: 1, key: 1 }, { unique: true });
itemSchema.index({ sourceId: 1, runId: 1, kind: 1, status: 1, id: 1 });

const auditSchema = new Schema<KnowledgeAuditRecord>({
  id: requiredString,
  sourceId: requiredString,
  actorId: String,
  action: requiredString,
  resourceId: requiredString,
  result: requiredString,
  at: { type: Date, required: true },
});
auditSchema.index({ id: 1 }, { unique: true });
auditSchema.index({ sourceId: 1, at: -1 });

export interface KnowledgeModels {
  KnowledgeSource: Model<KnowledgeSourceRecord>;
  KnowledgeNode: Model<KnowledgeNodeRecord>;
  KnowledgeDocument: Model<KnowledgeDocumentRecord>;
  KnowledgeRevision: Model<KnowledgeRevisionRecord>;
  KnowledgeAsset: Model<KnowledgeAssetRecord>;
  KnowledgeRun: Model<KnowledgeRunRecord>;
  KnowledgeItem: Model<KnowledgeItemRecord>;
  KnowledgeAudit: Model<KnowledgeAuditRecord>;
}

export function createKnowledgeModels(mongoose: typeof import('mongoose')): KnowledgeModels {
  return {
    KnowledgeSource:
      mongoose.models.KnowledgeSource || mongoose.model('KnowledgeSource', sourceSchema),
    KnowledgeNode: mongoose.models.KnowledgeNode || mongoose.model('KnowledgeNode', nodeSchema),
    KnowledgeDocument:
      mongoose.models.KnowledgeDocument || mongoose.model('KnowledgeDocument', documentSchema),
    KnowledgeRevision:
      mongoose.models.KnowledgeRevision || mongoose.model('KnowledgeRevision', revisionSchema),
    KnowledgeAsset: mongoose.models.KnowledgeAsset || mongoose.model('KnowledgeAsset', assetSchema),
    KnowledgeRun: mongoose.models.KnowledgeRun || mongoose.model('KnowledgeRun', runSchema),
    KnowledgeItem: mongoose.models.KnowledgeItem || mongoose.model('KnowledgeItem', itemSchema),
    KnowledgeAudit: mongoose.models.KnowledgeAudit || mongoose.model('KnowledgeAudit', auditSchema),
  };
}
