import { act, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import SyncPanel from '../SyncPanel';

const mockRuns = jest.fn();
const mockRun = jest.fn();
const mockMutate = jest.fn();
jest.mock('../queries', () => ({
  useKnowledgeSyncRuns: () => mockRuns(),
  useKnowledgeSyncRun: () => mockRun(),
  useKnowledgeSyncAction: () => ({ mutate: mockMutate, isLoading: false }),
}));
jest.mock('~/hooks', () => ({ useLocalize: () => (key: string) => key }));
jest.mock('~/utils', () => ({ cn: (...values: unknown[]) => values.filter(Boolean).join(' ') }));
jest.mock('@librechat/client', () => ({
  Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button {...props}>{children}</button>
  ),
  Spinner: () => <span />,
}));

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
);
const run = {
  id: 'run-1',
  mode: 'full',
  status: 'failed',
  phase: 'extract',
  counts: {},
  errorCode: 'private-source-token',
  items: [],
};

describe('SyncPanel', () => {
  beforeEach(() => {
    mockRuns.mockReturnValue({
      data: { pages: [{ items: [] }] },
      refetch: jest.fn(),
      isLoading: false,
      isError: false,
    });
    mockRun.mockReturnValue({ data: { pages: [run] }, isLoading: false, isError: false });
    Object.defineProperty(crypto, 'randomUUID', {
      configurable: true,
      value: jest.fn(() => 'request-123'),
    });
  });

  it('reuses the same idempotency key after an unsuccessful attempt', () => {
    render(<SyncPanel />, { wrapper });
    const button = screen.getByRole('button', { name: 'com_knowledge_sync_full' });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(mockMutate).toHaveBeenCalledTimes(2);
    expect(mockMutate.mock.calls[0][0]).toEqual({ mode: 'full', idempotencyKey: 'request-123' });
    expect(mockMutate.mock.calls[1][0]).toEqual(mockMutate.mock.calls[0][0]);
  });

  it('disables duplicate starts while a synchronization is running', () => {
    mockRuns.mockReturnValue({
      data: { pages: [{ items: [{ ...run, status: 'running' }] }] },
      refetch: jest.fn(),
    });
    render(<SyncPanel />, { wrapper });
    expect(screen.getByRole('button', { name: 'com_knowledge_sync_full' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'com_knowledge_sync_incremental' })).toBeDisabled();
  });

  it('retries a failed task and keeps raw error details out of the page', () => {
    mockRuns.mockReturnValue({ data: { pages: [{ items: [run] }] }, refetch: jest.fn() });
    render(<SyncPanel />, { wrapper });
    fireEvent.click(screen.getByRole('button', { name: /^com_knowledge_sync / }));
    fireEvent.click(
      screen.getByRole('button', { name: /com_knowledge_sync_full.*com_knowledge_status_failed/ }),
    );
    expect(screen.getByRole('alert')).toHaveTextContent('com_knowledge_sync_failed');
    expect(screen.queryByText('private-source-token')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'com_ui_retry' }));
    expect(mockMutate).toHaveBeenCalledWith(
      { retryId: 'run-1', idempotencyKey: 'request-123' },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it('reuses a retry key until success and creates a key for the next action', () => {
    const randomUUID = jest.mocked(crypto.randomUUID);
    randomUUID
      .mockReturnValueOnce('11111111-1111-4111-8111-111111111111')
      .mockReturnValueOnce('22222222-2222-4222-8222-222222222222');
    mockRuns.mockReturnValue({ data: { pages: [{ items: [run] }] }, refetch: jest.fn() });
    render(<SyncPanel />, { wrapper });
    fireEvent.click(screen.getByRole('button', { name: /^com_knowledge_sync / }));
    const retry = screen.getByRole('button', { name: 'com_ui_retry' });
    fireEvent.click(retry);
    fireEvent.click(retry);
    expect(mockMutate.mock.calls[0][0]).toEqual({
      retryId: 'run-1',
      idempotencyKey: '11111111-1111-4111-8111-111111111111',
    });
    expect(mockMutate.mock.calls[1][0]).toEqual(mockMutate.mock.calls[0][0]);
    expect(randomUUID).toHaveBeenCalledTimes(1);
    act(() => mockMutate.mock.calls[1][1].onSuccess({ id: 'run-2' }));
    fireEvent.click(retry);
    expect(mockMutate.mock.calls[2][0]).toEqual({
      retryId: 'run-1',
      idempotencyKey: '22222222-2222-4222-8222-222222222222',
    });
  });

  it('uses distinct item identities when two failures belong to one document', () => {
    const errors = jest.spyOn(console, 'error').mockImplementation(() => {});
    mockRuns.mockReturnValue({ data: { pages: [{ items: [run] }] }, refetch: jest.fn() });
    mockRun.mockReturnValue({
      data: {
        pages: [
          {
            ...run,
            items: [
              { id: 'item-1', documentId: 'doc-1', title: 'Shared document', status: 'failed' },
              { id: 'item-2', documentId: 'doc-1', title: 'Shared document', status: 'failed' },
            ],
          },
        ],
      },
      isLoading: false,
      isError: false,
    });
    render(<SyncPanel />, { wrapper });
    fireEvent.click(screen.getByRole('button', { name: /^com_knowledge_sync / }));
    fireEvent.click(
      screen.getByRole('button', { name: /com_knowledge_sync_full.*com_knowledge_status_failed/ }),
    );
    expect(screen.getAllByText('Shared document')).toHaveLength(2);
    expect(errors).not.toHaveBeenCalled();
    errors.mockRestore();
  });
});
