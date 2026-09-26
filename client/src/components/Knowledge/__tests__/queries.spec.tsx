import axios from 'axios';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  useKnowledgeDocument,
  useKnowledgeSyncAction,
  useKnowledgeSyncRuns,
  useKnowledgeTree,
} from '../queries';

const mockGet = jest.fn();
let mockUser = { id: 'user-1', role: 'USER' };
jest.mock('librechat-data-provider', () => ({
  apiBaseUrl: () => '',
  QueryKeys: { knowledgeAccess: 'knowledgeAccess' },
  SystemRoles: { ADMIN: 'ADMIN' },
  request: { get: (...args: unknown[]) => mockGet(...args) },
}));
jest.mock('~/hooks/AuthContext', () => ({
  useAuthContext: () => ({ isAuthenticated: true, user: mockUser }),
}));
jest.mock('~/data-provider', () => ({
  useGetStartupConfig: () => ({ data: { knowledge: { enabled: true } } }),
}));

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    logger: { log: () => {}, warn: () => {}, error: () => {} },
  });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}

describe('knowledge requests', () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockUser = { id: 'user-1', role: 'USER' };
  });

  it('does not request synchronization history as a partner', () => {
    const { wrapper } = setup();
    const { result } = renderHook(() => useKnowledgeSyncRuns(), { wrapper });
    expect(result.current.fetchStatus).toBe('idle');
    expect(mockGet).not.toHaveBeenCalled();
  });

  it('passes parent and cursor to successive directory pages', async () => {
    mockGet
      .mockResolvedValueOnce({ items: [], nextCursor: 'cursor-2', sourceStatus: 'ready' })
      .mockResolvedValueOnce({ items: [], sourceStatus: 'ready' });
    const { wrapper } = setup();
    const { result } = renderHook(() => useKnowledgeTree('parent-1'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    await act(async () => {
      await result.current.fetchNextPage();
    });
    expect(mockGet).toHaveBeenLastCalledWith(
      '/api/knowledge/tree',
      expect.objectContaining({ params: { parentId: 'parent-1', cursor: 'cursor-2' } }),
    );
  });

  it('separates cached documents when the signed-in user changes', async () => {
    mockGet.mockResolvedValueOnce({ id: 'doc-1', title: 'First user content' });
    const { wrapper } = setup();
    const { result, rerender } = renderHook(() => useKnowledgeDocument('doc-1'), { wrapper });
    await waitFor(() => expect(result.current.data?.title).toBe('First user content'));
    mockGet.mockImplementation(() => new Promise(() => {}));
    mockUser = { id: 'user-2', role: 'USER' };
    rerender();
    expect(result.current.data).toBeUndefined();
  });

  it('rechecks knowledge access after a denied request without retrying the document', async () => {
    mockGet.mockRejectedValue({ isAxiosError: true, response: { status: 403 } });
    const { client, wrapper } = setup();
    const invalidate = jest.spyOn(client, 'invalidateQueries');
    const { result } = renderHook(() => useKnowledgeDocument('doc-1'), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(mockGet).toHaveBeenCalledTimes(1);
    expect(invalidate).toHaveBeenCalledWith(['knowledgeAccess']);
  });

  it('submits sync mode with its idempotency header and refreshes knowledge queries', async () => {
    mockUser = { id: 'admin-1', role: 'ADMIN' };
    const post = jest.spyOn(axios, 'post').mockResolvedValue({ data: { id: 'run-1' } });
    const { client, wrapper } = setup();
    const invalidate = jest.spyOn(client, 'invalidateQueries');
    const { result } = renderHook(() => useKnowledgeSyncAction(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ mode: 'full', idempotencyKey: 'request-123' });
    });
    expect(post).toHaveBeenCalledWith(
      '/api/knowledge/sync-runs',
      { mode: 'full' },
      { headers: { 'Idempotency-Key': 'request-123' } },
    );
    expect(invalidate).toHaveBeenCalledWith(['knowledge', 'admin-1']);
  });

  it('submits a retry with the required idempotency header', async () => {
    mockUser = { id: 'admin-1', role: 'ADMIN' };
    const post = jest.spyOn(axios, 'post').mockResolvedValue({ data: { id: 'run-2' } });
    const { wrapper } = setup();
    const { result } = renderHook(() => useKnowledgeSyncAction(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ retryId: 'run-1', idempotencyKey: 'retry-request-123' });
    });
    expect(post).toHaveBeenCalledWith(
      '/api/knowledge/sync-runs/run-1/retry',
      {},
      { headers: { 'Idempotency-Key': 'retry-request-123' } },
    );
  });
});
