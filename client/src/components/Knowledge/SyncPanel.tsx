import { useEffect, useMemo, useRef } from 'react';
import { atom, useAtom } from 'jotai';
import { Button } from '@librechat/client';
import { useQueryClient } from '@tanstack/react-query';
import { ChevronDown, RefreshCw, RotateCcw } from 'lucide-react';
import type { KnowledgeSyncCounts } from 'librechat-data-provider';
import type { KnowledgeSyncAction } from './queries';
import type { TranslationKeys } from '~/hooks';
import {
  KnowledgeDate,
  KnowledgeError,
  KnowledgeLoading,
  KnowledgeStatus,
  KnowledgeSyncFailure,
  KnowledgeSyncPhase,
} from './Status';
import { useKnowledgeSyncAction, useKnowledgeSyncRun, useKnowledgeSyncRuns } from './queries';
import { useLocalize } from '~/hooks';
import { cn } from '~/utils';

const countLabels: Record<keyof KnowledgeSyncCounts, TranslationKeys> = {
  nodes: 'com_knowledge_count_nodes',
  documents: 'com_knowledge_count_documents',
  published: 'com_knowledge_count_published',
  unchanged: 'com_knowledge_count_unchanged',
  failed: 'com_knowledge_count_failed',
  unsupported: 'com_knowledge_count_unsupported',
  inaccessible: 'com_knowledge_count_inaccessible',
  removed: 'com_knowledge_count_removed',
  media: 'com_knowledge_count_media',
};

function SyncDetails({ runId }: { runId: string }) {
  const localize = useLocalize();
  const query = useKnowledgeSyncRun(runId);
  const run = query.data?.pages[0];
  if (query.isError) {
    return (
      <KnowledgeError
        sync
        error={query.error}
        retry={() => void query.refetch()}
        retrying={query.isFetching}
      />
    );
  }
  if (query.isLoading || !run) {
    return <KnowledgeLoading />;
  }

  return (
    <div className="space-y-4 border-t border-border-light bg-surface-primary p-4">
      <p className="text-sm text-text-secondary">
        <KnowledgeSyncPhase phase={run.phase} />
      </p>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {(Object.keys(countLabels) as Array<keyof KnowledgeSyncCounts>).map((key) => (
          <div key={key} className="rounded-md bg-surface-secondary p-3">
            <dt className="text-xs text-text-secondary">{localize(countLabels[key])}</dt>
            <dd className="mt-1 text-lg font-semibold tabular-nums text-text-primary">
              {run.counts[key]}
            </dd>
          </div>
        ))}
      </dl>
      {run.errorCode && (
        <p role="alert" className="text-sm text-text-secondary">
          <KnowledgeSyncFailure code={run.errorCode} />
        </p>
      )}
      <ul className="divide-y divide-border-light">
        {query.data?.pages
          .flatMap((page) => page.items)
          .map((item) => (
            <li
              key={item.id}
              className="flex flex-wrap items-center justify-between gap-2 py-3 text-sm"
            >
              <div className="min-w-0">
                <span className="break-words text-text-primary">{item.title}</span>
                {item.errorCode && (
                  <p className="mt-1 text-xs text-text-secondary">
                    <KnowledgeSyncFailure code={item.errorCode} />
                  </p>
                )}
              </div>
              <div className="flex items-center gap-2">
                {!!item.missing?.length && (
                  <span className="text-xs text-text-secondary">
                    {localize('com_knowledge_missing_items', { count: item.missing.length })}
                  </span>
                )}
                <KnowledgeStatus status={item.status} />
              </div>
            </li>
          ))}
      </ul>
      {query.hasNextPage && (
        <Button
          variant="outline"
          size="sm"
          disabled={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          {localize('com_ui_load_more')}
        </Button>
      )}
    </div>
  );
}

export default function SyncPanel() {
  const localize = useLocalize();
  const queryClient = useQueryClient();
  const query = useKnowledgeSyncRuns();
  const mutation = useKnowledgeSyncAction();
  const [selectedRunId, setSelectedRunId] = useAtom(
    useMemo(() => atom<string | undefined>(undefined), []),
  );
  const [expanded, setExpanded] = useAtom(useMemo(() => atom(false), []));
  const pendingAction = useRef<{ target: string; action: KnowledgeSyncAction }>();
  const runs = query.data?.pages.flatMap((page) => page.items) ?? [];
  const active = runs.some((run) => run.status === 'running' || run.status === 'queued');
  const previouslyActive = useRef(active);

  useEffect(() => {
    if (previouslyActive.current && !active && !query.isError) {
      void queryClient.invalidateQueries({
        predicate: (entry) => entry.queryKey[0] === 'knowledge' && entry.queryKey[2] === 'tree',
      });
    }
    previouslyActive.current = active;
  }, [active, query.isError, queryClient]);

  const submit = (action: { mode: 'full' | 'incremental' } | { retryId: string }) => {
    const target = 'mode' in action ? `mode:${action.mode}` : `retry:${action.retryId}`;
    if (pendingAction.current?.target !== target) {
      pendingAction.current = {
        target,
        action: { ...action, idempotencyKey: crypto.randomUUID() },
      };
    }
    const request = pendingAction.current.action;
    mutation.mutate(request, {
      onSuccess: (run) => {
        if (pendingAction.current?.action === request) {
          pendingAction.current = undefined;
        }
        setExpanded(true);
        setSelectedRunId(run.id);
      },
    });
  };

  return (
    <section
      aria-label={localize('com_knowledge_sync')}
      className="shrink-0 border-b border-border-light bg-surface-primary-alt"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        <Button
          variant="ghost"
          size="sm"
          aria-expanded={expanded}
          aria-controls="knowledge-sync-runs"
          onClick={() => setExpanded(!expanded)}
        >
          <ChevronDown
            className={cn('mr-2 size-4', !expanded && '-rotate-90')}
            aria-hidden="true"
          />
          {localize('com_knowledge_sync')}
          {runs[0] && (
            <span className="ml-3">
              <KnowledgeStatus status={runs[0].status} />
            </span>
          )}
        </Button>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={active || mutation.isLoading || query.isLoading}
            onClick={() => submit({ mode: 'incremental' })}
          >
            <RefreshCw className="mr-2 size-4" aria-hidden="true" />
            {localize('com_knowledge_sync_incremental')}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={active || mutation.isLoading || query.isLoading}
            onClick={() => submit({ mode: 'full' })}
          >
            {localize('com_knowledge_sync_full')}
          </Button>
        </div>
      </div>
      {mutation.isError && <KnowledgeError error={mutation.error} sync />}
      {expanded && (
        <div id="knowledge-sync-runs" className="max-h-[45vh] overflow-y-auto px-4 pb-4">
          <div className="mb-2 flex items-center justify-between gap-2 text-xs text-text-secondary">
            <span>
              {localize('com_knowledge_last_sync')}{' '}
              <KnowledgeDate value={query.data?.pages[0]?.lastCompleteScanAt} />
            </span>
            <Button
              variant="ghost"
              size="sm"
              disabled={query.isFetching}
              onClick={() => void query.refetch()}
            >
              {localize('com_ui_refresh')}
            </Button>
          </div>
          {query.isError && (
            <KnowledgeError
              sync
              error={query.error}
              retry={() => void query.refetch()}
              retrying={query.isFetching}
            />
          )}
          {!query.isError && query.isLoading && <KnowledgeLoading />}
          {!query.isError && !query.isLoading && runs.length === 0 && (
            <p className="py-6 text-center text-sm text-text-secondary">
              {localize('com_knowledge_no_sync_runs')}
            </p>
          )}
          {!query.isError && !query.isLoading && runs.length > 0 && (
            <ul className="space-y-2">
              {runs.map((run) => (
                <li key={run.id} className="overflow-hidden rounded-lg border border-border-light">
                  <div className="flex items-center gap-2 p-2">
                    <Button
                      variant="ghost"
                      className="h-auto min-w-0 flex-1 justify-start gap-3 px-2 py-2 text-left"
                      aria-expanded={selectedRunId === run.id}
                      onClick={() =>
                        setSelectedRunId(selectedRunId === run.id ? undefined : run.id)
                      }
                    >
                      <ChevronDown
                        className={cn('size-4 shrink-0', selectedRunId !== run.id && '-rotate-90')}
                        aria-hidden="true"
                      />
                      <span className="min-w-0 flex-1 text-sm">
                        <span className="block">
                          {localize(
                            run.mode === 'full'
                              ? 'com_knowledge_sync_full'
                              : 'com_knowledge_sync_incremental',
                          )}
                        </span>
                        <span className="mt-1 block text-xs font-normal text-text-secondary">
                          <KnowledgeDate value={run.startedAt ?? run.finishedAt} />
                        </span>
                      </span>
                      <KnowledgeStatus status={run.status} />
                    </Button>
                    {(run.status === 'failed' || run.status === 'partial') && (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={active || mutation.isLoading}
                        onClick={() => submit({ retryId: run.id })}
                      >
                        <RotateCcw className="mr-2 size-4" aria-hidden="true" />
                        {localize('com_ui_retry')}
                      </Button>
                    )}
                  </div>
                  {selectedRunId === run.id && <SyncDetails runId={run.id} />}
                </li>
              ))}
            </ul>
          )}
          {query.hasNextPage && (
            <Button
              variant="outline"
              size="sm"
              className="mt-3"
              disabled={query.isFetchingNextPage}
              onClick={() => void query.fetchNextPage()}
            >
              {localize('com_ui_load_more')}
            </Button>
          )}
        </div>
      )}
    </section>
  );
}
