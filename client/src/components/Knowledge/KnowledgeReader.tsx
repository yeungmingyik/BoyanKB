import { useEffect, useRef } from 'react';
import { Button } from '@librechat/client';
import { ArrowLeft, RefreshCw } from 'lucide-react';
import { Link, useLocation } from 'react-router-dom';
import { KnowledgeDate, KnowledgeError, KnowledgeLoading, KnowledgeStatus } from './Status';
import { useKnowledgeDocument } from './queries';
import { knowledgeHashAnchor } from './links';
import ReadingBlocks from './ReadingBlocks';
import { useLocalize } from '~/hooks';

export default function KnowledgeReader({
  documentId,
  revisionId,
}: {
  documentId: string;
  revisionId?: string;
}) {
  const localize = useLocalize();
  const location = useLocation();
  const articleRef = useRef<HTMLElement>(null);
  const query = useKnowledgeDocument(documentId, revisionId);
  const document = query.data;
  const readable = document && document.status !== 'removed' && document.status !== 'inaccessible';
  const searchReturn = location.state?.knowledgeSearch;
  const returnTo =
    typeof searchReturn === 'string' && /^\/knowledge\/search(?:\?[^#]*)?$/.test(searchReturn)
      ? searchReturn
      : '/knowledge';

  useEffect(() => {
    const anchor = knowledgeHashAnchor(location.hash);
    const article = articleRef.current;
    if (!anchor || !article || query.isError || query.isLoading) {
      return;
    }
    const target = article.ownerDocument.getElementById(anchor);
    if (target && article.contains(target)) {
      target.focus({ preventScroll: true });
      target.scrollIntoView({ block: 'center', behavior: 'auto' });
    }
  }, [location.hash, query.data, query.isError, query.isLoading]);

  return (
    <div className="mx-auto w-full max-w-4xl p-4 md:p-8">
      <div className="mb-6 flex items-center justify-between gap-3">
        <Link
          to={returnTo}
          className="focus-visible:ring-ring inline-flex items-center gap-2 rounded text-sm text-text-secondary hover:text-text-primary focus-visible:outline-none focus-visible:ring-2"
        >
          <ArrowLeft className="size-4" aria-hidden="true" />
          {localize(
            returnTo === '/knowledge' ? 'com_knowledge_directory' : 'com_knowledge_search_results',
          )}
        </Link>
        <Button
          variant="ghost"
          size="sm"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          <RefreshCw className="mr-2 size-4" aria-hidden="true" />
          {localize('com_ui_refresh')}
        </Button>
      </div>
      {query.isError && (
        <KnowledgeError
          error={query.error}
          retry={() => void query.refetch()}
          retrying={query.isFetching}
        />
      )}
      {!query.isError && query.isLoading && <KnowledgeLoading />}
      {!query.isError && !query.isLoading && readable && (
        <article
          ref={articleRef}
          aria-labelledby="knowledge-document-title"
          className="min-w-0 text-text-primary"
        >
          <header className="mb-6 border-b border-border-light pb-6">
            <h1
              id="knowledge-document-title"
              className="break-words text-2xl font-semibold md:text-3xl"
            >
              {document.title}
            </h1>
            <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-text-secondary">
              <KnowledgeStatus status={document.status} />
              <span>
                {localize('com_knowledge_source_updated')}{' '}
                <KnowledgeDate value={document.revision.sourceUpdatedAt} />
              </span>
              <span>
                {localize('com_knowledge_published')}{' '}
                <KnowledgeDate value={document.revision.publishedAt} />
              </span>
            </div>
            {revisionId && (
              <div className="mt-4 flex flex-wrap items-center gap-3 text-sm text-text-secondary">
                <span>{localize('com_knowledge_revision')}</span>
                <Link
                  to={`/knowledge/documents/${encodeURIComponent(documentId)}`}
                  className="focus-visible:ring-ring rounded underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2"
                >
                  {localize('com_knowledge_latest_revision')}
                </Link>
              </div>
            )}
            {document.status !== 'published' && (
              <p role="status" className="mt-3 text-sm text-text-secondary">
                {localize('com_knowledge_previous_revision')}
              </p>
            )}
          </header>
          {document.blocks.length > 0 ? (
            <ReadingBlocks blocks={document.blocks} assets={document.assets} />
          ) : (
            <p className="text-text-secondary">{localize('com_knowledge_empty_document')}</p>
          )}
        </article>
      )}
      {!query.isError && !query.isLoading && !readable && (
        <KnowledgeError error={{ status: 404 }} />
      )}
    </div>
  );
}
