import { BookOpen } from 'lucide-react';
import { SystemRoles } from 'librechat-data-provider';
import { Link, Navigate, useMatch } from 'react-router-dom';
import OpenSidebar from '~/components/Chat/Menus/OpenSidebar';
import { useGetStartupConfig } from '~/data-provider';
import { useAuthContext, useLocalize } from '~/hooks';
import KnowledgeReader from './KnowledgeReader';
import KnowledgeSearch from './KnowledgeSearch';
import KnowledgeTree from './KnowledgeTree';
import SyncPanel from './SyncPanel';
import { cn } from '~/utils';

export default function KnowledgePage() {
  const localize = useLocalize();
  const { user } = useAuthContext();
  const { data: config } = useGetStartupConfig();
  const revisionMatch = useMatch('/knowledge/documents/:documentId/revisions/:revisionId');
  const documentMatch = useMatch('/knowledge/documents/:documentId');
  const documentId = revisionMatch?.params.documentId ?? documentMatch?.params.documentId;
  const revisionId = revisionMatch?.params.revisionId;
  const isSearching = !!useMatch('/knowledge/search');

  if (config && config.knowledge?.enabled !== true) {
    return <Navigate to="/c/new" replace={true} />;
  }

  return (
    <main className="flex h-full min-w-0 flex-col bg-surface-primary text-text-primary">
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border-light px-3 md:px-5">
        <div className="md:hidden">
          <OpenSidebar testId="knowledge-open-sidebar" />
        </div>
        <BookOpen className="size-5 shrink-0 text-text-secondary" aria-hidden="true" />
        <h1 className="min-w-0 truncate text-lg font-semibold">
          {localize('com_knowledge_library')}
        </h1>
        <Link
          to={isSearching ? '/knowledge' : '/knowledge/search'}
          className="focus-visible:ring-ring ml-auto shrink-0 rounded-lg px-3 py-2 text-sm text-text-secondary hover:bg-surface-hover focus-visible:outline-none focus-visible:ring-2"
        >
          {localize(isSearching ? 'com_knowledge_directory' : 'com_knowledge_search')}
        </Link>
      </header>
      {user?.role === SystemRoles.ADMIN && <SyncPanel />}
      {isSearching ? (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <KnowledgeSearch />
        </div>
      ) : (
        <div className="flex min-h-0 flex-1">
          <aside
            className={cn(
              'w-full shrink-0 overflow-y-auto border-r border-border-light lg:block lg:w-80',
              documentId && 'hidden',
            )}
          >
            <KnowledgeTree selectedDocumentId={documentId} />
          </aside>
          <div className={cn('min-w-0 flex-1 overflow-y-auto', !documentId && 'hidden lg:block')}>
            {documentId ? (
              <KnowledgeReader
                key={`${documentId}:${revisionId ?? ''}`}
                documentId={documentId}
                revisionId={revisionId}
              />
            ) : (
              <div className="flex h-full min-h-64 flex-col items-center justify-center gap-4 p-8 text-center text-text-secondary">
                <BookOpen className="size-10" aria-hidden="true" />
                <p>{localize('com_knowledge_select_document')}</p>
              </div>
            )}
          </div>
        </div>
      )}
    </main>
  );
}
