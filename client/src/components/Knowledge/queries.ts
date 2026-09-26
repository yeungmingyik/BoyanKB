import axios from 'axios';
import { apiBaseUrl, QueryKeys, request, SystemRoles } from 'librechat-data-provider';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type {
  KnowledgeDocumentResponse,
  KnowledgeSyncRun,
  KnowledgeSyncRunResponse,
  KnowledgeSyncRunsResponse,
  KnowledgeTreeResponse,
} from 'librechat-data-provider';
import { useGetStartupConfig } from '~/data-provider';
import { useAuthContext } from '~/hooks/AuthContext';

const knowledgeURL = (path: string) => `${apiBaseUrl()}/api/knowledge${path}`;

function useKnowledgeSession() {
  const { user, isAuthenticated } = useAuthContext();
  const { data: config } = useGetStartupConfig();
  const queryClient = useQueryClient();
  const enabled = isAuthenticated && config?.knowledge?.enabled === true;

  return {
    userId: user?.id,
    enabled,
    isAdmin: user?.role === SystemRoles.ADMIN,
    onError: (error: unknown) => {
      if (axios.isAxiosError(error) && error.response?.status === 403) {
        void queryClient.invalidateQueries([QueryKeys.knowledgeAccess]);
      }
    },
  };
}

export function useKnowledgeTree(parentId?: string) {
  const session = useKnowledgeSession();
  return useInfiniteQuery<KnowledgeTreeResponse>({
    queryKey: ['knowledge', session.userId, 'tree', parentId],
    queryFn: ({ pageParam, signal }) =>
      request.get<KnowledgeTreeResponse>(knowledgeURL('/tree'), {
        signal,
        params: { parentId, cursor: pageParam },
      }),
    getNextPageParam: (page) => page.nextCursor,
    enabled: session.enabled,
    retry: false,
    cacheTime: 0,
    staleTime: 0,
    onError: session.onError,
  });
}

export function useKnowledgeDocument(documentId?: string, revisionId?: string) {
  const session = useKnowledgeSession();
  return useQuery<KnowledgeDocumentResponse>({
    queryKey: ['knowledge', session.userId, 'document', documentId, revisionId],
    queryFn: ({ signal }) =>
      request.get<KnowledgeDocumentResponse>(
        knowledgeURL(
          `/documents/${encodeURIComponent(documentId ?? '')}${
            revisionId ? `/revisions/${encodeURIComponent(revisionId)}` : ''
          }`,
        ),
        { signal },
      ),
    enabled: session.enabled && !!documentId,
    retry: false,
    cacheTime: 0,
    staleTime: 0,
    onError: session.onError,
  });
}

export function useKnowledgeSyncRuns() {
  const session = useKnowledgeSession();
  return useInfiniteQuery<KnowledgeSyncRunsResponse>({
    queryKey: ['knowledge', session.userId, 'sync-runs'],
    queryFn: ({ pageParam, signal }) =>
      request.get<KnowledgeSyncRunsResponse>(knowledgeURL('/sync-runs'), {
        signal,
        params: { cursor: pageParam },
      }),
    getNextPageParam: (page) => page.nextCursor,
    enabled: session.enabled && session.isAdmin,
    retry: false,
    cacheTime: 0,
    staleTime: 0,
    refetchInterval: (data) =>
      data?.pages.some((page) =>
        page.items.some((run) => run.status === 'queued' || run.status === 'running'),
      )
        ? 5000
        : false,
    onError: session.onError,
  });
}

export function useKnowledgeSyncRun(runId?: string) {
  const session = useKnowledgeSession();
  return useInfiniteQuery<KnowledgeSyncRunResponse>({
    queryKey: ['knowledge', session.userId, 'sync-run', runId],
    queryFn: ({ pageParam, signal }) =>
      request.get<KnowledgeSyncRunResponse>(
        knowledgeURL(`/sync-runs/${encodeURIComponent(runId ?? '')}`),
        { signal, params: { cursor: pageParam } },
      ),
    getNextPageParam: (page) => page.nextCursor,
    enabled: session.enabled && session.isAdmin && !!runId,
    retry: false,
    cacheTime: 0,
    staleTime: 0,
    refetchInterval: (data) =>
      data?.pages[0]?.status === 'running' || data?.pages[0]?.status === 'queued' ? 5000 : false,
    onError: session.onError,
  });
}

export type KnowledgeSyncAction = { idempotencyKey: string } & (
  | { mode: 'full' | 'incremental' }
  | { retryId: string }
);

export function useKnowledgeSyncAction() {
  const session = useKnowledgeSession();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (action: KnowledgeSyncAction) => {
      const retryId = 'retryId' in action ? action.retryId : undefined;
      const response = await axios.post<KnowledgeSyncRun>(
        knowledgeURL(`/sync-runs${retryId ? `/${encodeURIComponent(retryId)}/retry` : ''}`),
        'mode' in action ? { mode: action.mode } : {},
        { headers: { 'Idempotency-Key': action.idempotencyKey } },
      );
      return response.data;
    },
    onSuccess: () => void queryClient.invalidateQueries(['knowledge', session.userId]),
    onError: session.onError,
    retry: false,
  });
}

export function fetchKnowledgeAsset(assetId: string, signal?: AbortSignal): Promise<Blob> {
  return request.get<Blob>(knowledgeURL(`/assets/${encodeURIComponent(assetId)}`), {
    responseType: 'blob',
    signal,
  });
}

export function useKnowledgeAsset(assetId: string, enabled: boolean) {
  const session = useKnowledgeSession();
  return useQuery({
    queryKey: ['knowledge', session.userId, 'asset', assetId],
    queryFn: ({ signal }) => fetchKnowledgeAsset(assetId, signal),
    enabled: session.enabled && enabled,
    retry: false,
    cacheTime: 0,
    staleTime: 0,
    onError: session.onError,
  });
}
