import { useEffect, useMemo, useRef } from 'react';
import { atom, useAtom } from 'jotai';
import { Button, Input } from '@librechat/client';
import { ChevronDown, Search } from 'lucide-react';
import { Link, useLocation, useSearchParams } from 'react-router-dom';
import type { KnowledgeSearchMode, KnowledgeTreeNode } from 'librechat-data-provider';
import { KnowledgeDate, KnowledgeError, KnowledgeLoading } from './Status';
import { knowledgeSearchQueryLimit, useKnowledgeSearch } from './queries';
import { knowledgeReferenceHref } from './links';
import KnowledgeTree from './KnowledgeTree';
import { useLocalize } from '~/hooks';

export default function KnowledgeSearch() {
  const localize = useLocalize();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const searchText = (params.get('q') ?? '').trim();
  const requestedMode = params.get('mode');
  const mode: KnowledgeSearchMode =
    requestedMode === 'keyword' || requestedMode === 'semantic' ? requestedMode : 'hybrid';
  const directoryId = params.get('directory') || undefined;
  const [draft, setDraft] = useAtom(useMemo(() => atom(''), []));
  const [selectedDirectory, setSelectedDirectory] = useAtom(
    useMemo(() => atom<{ id: string; title: string } | undefined>(undefined), []),
  );
  const directoryPicker = useRef<HTMLDetailsElement>(null);
  const invalidQuery = searchText.length > knowledgeSearchQueryLimit;
  const query = useKnowledgeSearch(
    searchText ? { query: searchText, mode, directoryId, limit: 10 } : undefined,
  );
  const results = useMemo(() => {
    const items = query.data?.pages.flatMap((page) => page.items) ?? [];
    return [
      ...new Map(
        items.map((item) => [`${item.documentId}:${item.revisionId}:${item.blockId}`, item]),
      ).values(),
    ];
  }, [query.data]);

  useEffect(() => setDraft(searchText), [searchText, setDraft]);

  const updateParams = (values: { q?: string; mode?: KnowledgeSearchMode; directory?: string }) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(values)) {
      if (value) {
        next.set(key, value);
      } else {
        next.delete(key);
      }
    }
    setParams(next);
  };

  const selectDirectory = (node?: KnowledgeTreeNode) => {
    setSelectedDirectory(node ? { id: node.id, title: node.title } : undefined);
    updateParams({ directory: node?.id });
    if (directoryPicker.current) {
      directoryPicker.current.open = false;
    }
  };
  let scopeLabel = localize('com_knowledge_search_all');
  if (directoryId) {
    scopeLabel =
      selectedDirectory?.id === directoryId
        ? selectedDirectory.title
        : localize('com_knowledge_search_selected_directory');
  }

  return (
    <section
      className="mx-auto w-full max-w-4xl p-4 md:p-8"
      aria-labelledby="knowledge-search-title"
    >
      <h2 id="knowledge-search-title" className="mb-5 text-xl font-semibold">
        {localize('com_knowledge_search')}
      </h2>
      <form
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          const text = draft.trim();
          if (!text || text.length > knowledgeSearchQueryLimit) {
            return;
          }
          if (text === searchText) {
            void query.restart();
          } else {
            updateParams({ q: text });
          }
        }}
        className="space-y-3"
      >
        <div className="flex items-center gap-2">
          <Input
            type="search"
            value={draft}
            maxLength={knowledgeSearchQueryLimit}
            onChange={(event) => setDraft(event.target.value)}
            aria-label={localize('com_knowledge_search_query')}
            placeholder={localize('com_knowledge_search_placeholder')}
            className="min-w-0 flex-1"
          />
          <Button
            type="submit"
            disabled={!draft.trim() || draft.trim().length > knowledgeSearchQueryLimit}
          >
            <Search className="mr-2 size-4" aria-hidden="true" />
            {localize('com_ui_search')}
          </Button>
        </div>
        <div className="flex flex-wrap items-start gap-3">
          <label className="flex items-center gap-2 text-sm text-text-secondary">
            {localize('com_knowledge_search_mode')}
            <select
              value={mode}
              onChange={(event) =>
                updateParams({ mode: event.target.value as KnowledgeSearchMode })
              }
              className="focus-visible:ring-ring h-10 rounded-lg border border-border-medium bg-surface-primary px-3 text-sm text-text-primary focus-visible:outline-none focus-visible:ring-2"
            >
              <option value="hybrid">{localize('com_knowledge_search_hybrid')}</option>
              <option value="keyword">{localize('com_knowledge_search_keyword')}</option>
              <option value="semantic">{localize('com_knowledge_search_semantic')}</option>
            </select>
          </label>
          <details
            ref={directoryPicker}
            className="min-w-0 flex-1 basis-52 rounded-lg border border-border-medium"
          >
            <summary className="focus-visible:ring-ring flex min-h-10 cursor-pointer list-none items-center justify-between gap-2 rounded-lg px-3 text-sm focus-visible:outline-none focus-visible:ring-2">
              <span className="min-w-0 break-words">
                {localize('com_knowledge_search_scope')}
                {': '}
                {scopeLabel}
              </span>
              <ChevronDown className="size-4 shrink-0" aria-hidden="true" />
            </summary>
            <div className="max-h-80 overflow-y-auto border-t border-border-light">
              <Button
                type="button"
                variant="ghost"
                className="m-2"
                onClick={() => selectDirectory()}
              >
                {localize('com_knowledge_search_all')}
              </Button>
              <KnowledgeTree selectedNodeId={directoryId} onSelectNode={selectDirectory} />
            </div>
          </details>
        </div>
      </form>
      {!searchText && (
        <p role="status" className="py-12 text-center text-sm text-text-secondary">
          {localize('com_knowledge_search_start')}
        </p>
      )}
      {invalidQuery && (
        <p role="alert" className="py-8 text-center text-sm text-text-secondary">
          {localize('com_knowledge_search_invalid')}
        </p>
      )}
      {!!searchText && !invalidQuery && query.isError && (
        <KnowledgeError
          error={query.error}
          search={true}
          retry={() => void query.restart()}
          retrying={query.isFetching}
        />
      )}
      {!!searchText && !invalidQuery && !query.isError && query.isLoading && <KnowledgeLoading />}
      {!!searchText && !invalidQuery && !query.isError && !query.isLoading && (
        <div className="mt-6" aria-busy={query.isFetching}>
          <p role="status" className="mb-4 break-words text-sm text-text-secondary">
            {results.length > 0
              ? localize('com_knowledge_search_results_for', {
                  query: searchText,
                  count: results.length,
                })
              : localize('com_knowledge_search_empty')}
          </p>
          <ul className="space-y-3">
            {results.map((result) => {
              const href = knowledgeReferenceHref(result);
              return (
                <li
                  key={`${result.documentId}:${result.revisionId}:${result.blockId}`}
                  className="min-w-0 rounded-xl border border-border-light p-4"
                >
                  <h3 className="break-words font-semibold">
                    {href ? (
                      <Link
                        to={href}
                        state={{ knowledgeSearch: `${location.pathname}${location.search}` }}
                        className="focus-visible:ring-ring rounded text-text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2"
                      >
                        {result.title}
                      </Link>
                    ) : (
                      result.title
                    )}
                  </h3>
                  <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-text-secondary">
                    {result.snippet}
                  </p>
                  {result.sourceUpdatedAt && (
                    <p className="mt-3 text-xs text-text-secondary">
                      {localize('com_knowledge_source_updated')}{' '}
                      <KnowledgeDate value={result.sourceUpdatedAt} />
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
          {query.hasNextPage && (
            <Button
              type="button"
              variant="outline"
              className="mt-5 w-full"
              disabled={query.isFetching}
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
