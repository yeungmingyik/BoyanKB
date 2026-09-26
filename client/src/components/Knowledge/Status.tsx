import { AlertCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button, Spinner } from '@librechat/client';
import type {
  KnowledgeDocumentStatus,
  KnowledgeSourceStatus,
  KnowledgeSyncRun,
} from 'librechat-data-provider';
import type { TranslationKeys } from '~/hooks';
import { getResponseStatus } from '~/utils/errors';
import { useLocalize } from '~/hooks';

const statusKeys: Record<
  KnowledgeDocumentStatus | KnowledgeSourceStatus | KnowledgeSyncRun['status'],
  TranslationKeys
> = {
  pending: 'com_knowledge_status_pending',
  extracting: 'com_knowledge_status_extracting',
  indexing: 'com_knowledge_status_indexing',
  published: 'com_knowledge_status_published',
  partial: 'com_knowledge_status_partial',
  failed: 'com_knowledge_status_failed',
  unsupported: 'com_knowledge_status_unsupported',
  inaccessible: 'com_knowledge_status_inaccessible',
  removed: 'com_knowledge_status_removed',
  ready: 'com_knowledge_status_ready',
  paused: 'com_knowledge_status_paused',
  error: 'com_knowledge_status_error',
  queued: 'com_knowledge_status_queued',
  running: 'com_knowledge_status_running',
  completed: 'com_knowledge_status_completed',
};

export function KnowledgeStatus({ status }: { status: keyof typeof statusKeys }) {
  const localize = useLocalize();
  return (
    <span className="inline-flex rounded-md bg-surface-secondary px-2 py-1 text-xs text-text-secondary">
      {localize(statusKeys[status] ?? 'com_knowledge_status_pending')}
    </span>
  );
}

export function KnowledgeDate({ value }: { value?: string }) {
  const { i18n } = useTranslation();
  if (!value || !Number.isFinite(Date.parse(value))) {
    return <span>—</span>;
  }
  return (
    <time dateTime={value}>
      {new Intl.DateTimeFormat(i18n.language, {
        dateStyle: 'medium',
        timeStyle: 'short',
      }).format(new Date(value))}
    </time>
  );
}

export function KnowledgeLoading() {
  const localize = useLocalize();
  return (
    <div role="status" className="flex items-center justify-center gap-3 p-8 text-text-secondary">
      <Spinner className="size-5" />
      <span>{localize('com_ui_loading')}</span>
    </div>
  );
}

export function KnowledgeError({
  error,
  retry,
  retrying,
  sync,
}: {
  error: unknown;
  retry?: () => void;
  retrying?: boolean;
  sync?: boolean;
}) {
  const localize = useLocalize();
  const status = getResponseStatus(error);
  const messages: Record<number, TranslationKeys> = {
    403: 'com_knowledge_access_denied',
    404: 'com_knowledge_document_unavailable',
    410: 'com_knowledge_document_unavailable',
    409: 'com_knowledge_sync_conflict',
    503: 'com_knowledge_source_unavailable',
  };
  let message = messages[status ?? 0] ?? 'com_knowledge_load_failed';
  const code = (error as { response?: { data?: { code?: string } } })?.response?.data?.code;
  if (
    sync &&
    (code === 'KNOWLEDGE_SYNC_NOT_CONFIGURED' || code === 'KNOWLEDGE_SOURCE_UNCONFIGURED')
  ) {
    message = 'com_knowledge_sync_not_configured';
  }
  return (
    <div
      role="alert"
      className="flex flex-col items-center gap-3 p-6 text-center text-text-secondary"
    >
      <AlertCircle className="size-5" aria-hidden="true" />
      <p>{localize(message)}</p>
      {retry && status !== 403 && status !== 404 && status !== 410 && (
        <Button variant="outline" size="sm" disabled={retrying} onClick={retry}>
          {localize('com_ui_retry')}
        </Button>
      )}
    </div>
  );
}

export function KnowledgeSyncFailure({ code }: { code: string }) {
  const localize = useLocalize();
  const messages = new Map<string, TranslationKeys>([
    ['KNOWLEDGE_EXTRACTION_PARTIAL', 'com_knowledge_failure_partial'],
    ['KNOWLEDGE_FORMAT_UNSUPPORTED', 'com_knowledge_status_unsupported'],
    ['KNOWLEDGE_OUT_OF_SCOPE', 'com_knowledge_failure_out_of_scope'],
    ['KNOWLEDGE_SOURCE_VERSION_CHANGED', 'com_knowledge_failure_version_changed'],
    ['FEISHU_DOCUMENT_DENIED', 'com_knowledge_status_inaccessible'],
    ['KNOWLEDGE_DOCUMENT_REMOVED', 'com_knowledge_status_removed'],
  ]);
  return <span>{localize(messages.get(code) ?? 'com_knowledge_sync_failed')}</span>;
}

export function KnowledgeSyncPhase({ phase }: { phase: string }) {
  const localize = useLocalize();
  const phases = new Map<string, TranslationKeys>([
    ['validate', 'com_knowledge_phase_validate'],
    ['enumerate', 'com_knowledge_phase_enumerate'],
    ['extract', 'com_knowledge_phase_extract'],
    ['reconcile', 'com_knowledge_phase_reconcile'],
    ['finish', 'com_knowledge_status_completed'],
  ]);
  const label = phases.get(phase);
  return label ? <span>{localize(label)}</span> : null;
}
