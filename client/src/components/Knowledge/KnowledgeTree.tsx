import { useId, useMemo } from 'react';
import { atom, useAtom } from 'jotai';
import { Link } from 'react-router-dom';
import { Button } from '@librechat/client';
import { ChevronRight, FileText, FolderOpen, RefreshCw } from 'lucide-react';
import type { KnowledgeTreeNode } from 'librechat-data-provider';
import { KnowledgeDate, KnowledgeError, KnowledgeLoading, KnowledgeStatus } from './Status';
import { useKnowledgeTree } from './queries';
import { useLocalize } from '~/hooks';
import { cn } from '~/utils';

type TreeSelection = {
  selectedDocumentId?: string;
  selectedNodeId?: string;
  onSelectNode?: (node: KnowledgeTreeNode) => void;
};

function TreeNode({
  node,
  selectedDocumentId,
  selectedNodeId,
  onSelectNode,
  ancestors,
}: {
  node: KnowledgeTreeNode;
  ancestors: string[];
} & TreeSelection) {
  const localize = useLocalize();
  const childrenId = useId();
  const [expanded, setExpanded] = useAtom(useMemo(() => atom(false), []));
  const selected = onSelectNode
    ? node.id === selectedNodeId
    : node.documentId === selectedDocumentId;
  const expandable = node.hasChildren && !ancestors.includes(node.id);

  return (
    <li>
      <div
        className={cn(
          'flex min-w-0 items-start gap-1 rounded-lg px-1 py-1.5',
          selected ? 'bg-surface-active-alt' : 'hover:bg-surface-hover',
        )}
      >
        {expandable ? (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-8 shrink-0"
            aria-label={`${localize(expanded ? 'com_knowledge_collapse' : 'com_knowledge_expand')} ${node.title}`}
            aria-expanded={expanded}
            aria-controls={childrenId}
            onClick={() => setExpanded(!expanded)}
          >
            <ChevronRight className={cn('size-4', expanded && 'rotate-90')} aria-hidden="true" />
          </Button>
        ) : (
          <span className="flex size-8 shrink-0 items-center justify-center text-text-secondary">
            <FileText className="size-4" aria-hidden="true" />
          </span>
        )}
        <div className="min-w-0 flex-1 py-1">
          {onSelectNode && (
            <button
              type="button"
              aria-pressed={selected}
              onClick={() => onSelectNode(node)}
              className="focus-visible:ring-ring block w-full break-words rounded text-left text-sm font-medium text-text-primary focus-visible:outline-none focus-visible:ring-2"
            >
              {node.title}
            </button>
          )}
          {!onSelectNode &&
            (node.readable ? (
              <Link
                to={`/knowledge/documents/${encodeURIComponent(node.documentId)}`}
                aria-current={selected ? 'page' : undefined}
                className="focus-visible:ring-ring block break-words rounded text-sm font-medium text-text-primary focus-visible:outline-none focus-visible:ring-2"
              >
                {node.title}
              </Link>
            ) : (
              <span className="block break-words text-sm text-text-secondary">{node.title}</span>
            ))}
          {node.status !== 'published' && (
            <div className="mt-1">
              <KnowledgeStatus status={node.status} />
            </div>
          )}
        </div>
      </div>
      {expandable && expanded && (
        <div id={childrenId} className="ml-4 border-l border-border-light pl-2">
          <TreeBranch
            parentId={node.id}
            selectedDocumentId={selectedDocumentId}
            selectedNodeId={selectedNodeId}
            onSelectNode={onSelectNode}
            ancestors={[...ancestors, node.id]}
          />
        </div>
      )}
    </li>
  );
}

function TreeBranch({
  parentId,
  selectedDocumentId,
  selectedNodeId,
  onSelectNode,
  ancestors = [],
}: {
  parentId?: string;
  ancestors?: string[];
} & TreeSelection) {
  const localize = useLocalize();
  const query = useKnowledgeTree(parentId);
  const metadata = query.data?.pages[0];
  const unavailable = metadata?.sourceStatus === 'paused' || metadata?.sourceStatus === 'error';
  const nodes = useMemo(() => {
    const items = query.data?.pages.flatMap((page) => page.items) ?? [];
    return [...new Map(items.map((item) => [item.id, item])).values()];
  }, [query.data]);

  if (query.isError) {
    return (
      <KnowledgeError
        error={query.error}
        retry={() => void query.refetch()}
        retrying={query.isFetching}
      />
    );
  }
  if (query.isLoading) {
    return <KnowledgeLoading />;
  }

  return (
    <>
      {!parentId && (
        <div className="mb-3 flex items-start justify-between gap-2 border-b border-border-light pb-3">
          <div className="space-y-2 text-xs text-text-secondary">
            {metadata && <KnowledgeStatus status={metadata.sourceStatus} />}
            <p>
              {localize('com_knowledge_last_sync')}{' '}
              <KnowledgeDate value={metadata?.lastCompleteScanAt} />
            </p>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-8 shrink-0"
            aria-label={localize('com_ui_refresh')}
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
          >
            <RefreshCw
              className={cn('size-4', query.isFetching && 'animate-spin')}
              aria-hidden="true"
            />
          </Button>
        </div>
      )}
      {unavailable && (
        <p role="status" className="p-4 text-sm text-text-secondary">
          {localize('com_knowledge_source_unavailable')}
        </p>
      )}
      {!unavailable && nodes.length === 0 && (
        <p role="status" className="p-4 text-sm text-text-secondary">
          {localize('com_knowledge_empty')}
        </p>
      )}
      {!unavailable && nodes.length > 0 && (
        <ul className="space-y-1">
          {nodes.map((node) => (
            <TreeNode
              key={node.id}
              node={node}
              selectedDocumentId={selectedDocumentId}
              selectedNodeId={selectedNodeId}
              onSelectNode={onSelectNode}
              ancestors={ancestors}
            />
          ))}
        </ul>
      )}
      {query.hasNextPage && metadata?.sourceStatus === 'ready' && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="mt-3 w-full"
          disabled={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          {localize('com_ui_load_more')}
        </Button>
      )}
    </>
  );
}

export default function KnowledgeTree({
  selectedDocumentId,
  selectedNodeId,
  onSelectNode,
}: TreeSelection) {
  const localize = useLocalize();
  return (
    <nav aria-label={localize('com_knowledge_directory')} className="p-4">
      <h2 className="mb-4 flex items-center gap-2 font-semibold text-text-primary">
        <FolderOpen className="size-4" aria-hidden="true" />
        {localize('com_knowledge_directory')}
      </h2>
      <TreeBranch
        selectedDocumentId={selectedDocumentId}
        selectedNodeId={selectedNodeId}
        onSelectNode={onSelectNode}
      />
    </nav>
  );
}
