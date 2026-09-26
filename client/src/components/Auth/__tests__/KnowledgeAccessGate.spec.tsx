import React from 'react';
import userEvent from '@testing-library/user-event';
import { QueryKeys, dataService } from 'librechat-data-provider';
import { act, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import KnowledgeAccessGate from '../KnowledgeAccessGate';

jest.mock('librechat-data-provider', () => {
  const actual = jest.requireActual('librechat-data-provider');
  return {
    ...actual,
    dataService: { ...actual.dataService, getKnowledgeAccess: jest.fn() },
  };
});

jest.mock('@librechat/client', () => ({
  Button: ({
    variant: _variant,
    ...props
  }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant: string }) => <button {...props} />,
  Spinner: () => <span>{'Loading'}</span>,
}));

const mockLogout = jest.fn();
const mockUseAuthContext = jest.fn();
const mockUseGetStartupConfig = jest.fn();

jest.mock('~/hooks/AuthContext', () => ({
  useAuthContext: () => mockUseAuthContext(),
}));

jest.mock('~/data-provider', () => ({
  useGetStartupConfig: () => mockUseGetStartupConfig(),
}));

jest.mock('~/hooks', () => ({
  useLocalize: () => (key: string) => key,
}));

function renderGate() {
  const content = 'Private conversations';
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
    logger: { log: jest.fn(), warn: jest.fn(), error: jest.fn() },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <KnowledgeAccessGate>
        <div>{content}</div>
      </KnowledgeAccessGate>
    </QueryClientProvider>,
  );
  return queryClient;
}

beforeEach(() => {
  mockUseAuthContext.mockReturnValue({
    isAuthenticated: true,
    user: { id: 'partner' },
    logout: mockLogout,
  });
  mockUseGetStartupConfig.mockReturnValue({
    data: { appTitle: 'BoyanKB', knowledge: { enabled: true, agentId: 'knowledge' } },
    isError: false,
    isFetching: false,
    refetch: jest.fn(),
  });
});

it('does not mount the chat while access is being verified', async () => {
  jest.spyOn(dataService, 'getKnowledgeAccess').mockReturnValue(new Promise(() => {}));
  renderGate();
  expect(screen.getByRole('status', { name: 'com_ui_loading' })).toBeInTheDocument();
  expect(screen.queryByText('Private conversations')).not.toBeInTheDocument();
  await waitFor(() => expect(dataService.getKnowledgeAccess).toHaveBeenCalledTimes(1));
});

it('allows authorized users to reach the chat', async () => {
  jest
    .spyOn(dataService, 'getKnowledgeAccess')
    .mockResolvedValue({ access: true, configured: true });
  renderGate();
  expect(await screen.findByText('Private conversations')).toBeInTheDocument();
});

it('denies access without triggering logout and allows a grant to be retried', async () => {
  const request = jest
    .spyOn(dataService, 'getKnowledgeAccess')
    .mockResolvedValueOnce({ access: false, configured: true })
    .mockResolvedValueOnce({ access: true, configured: true });
  renderGate();
  expect(await screen.findByText('com_knowledge_access_denied')).toBeInTheDocument();
  expect(screen.queryByText('Private conversations')).not.toBeInTheDocument();
  expect(mockLogout).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: 'com_ui_retry' }));
  expect(await screen.findByText('Private conversations')).toBeInTheDocument();
  expect(request).toHaveBeenCalledTimes(2);
});

it('removes the chat when authorization is revoked', async () => {
  jest
    .spyOn(dataService, 'getKnowledgeAccess')
    .mockResolvedValueOnce({ access: true, configured: true })
    .mockResolvedValueOnce({ access: false, configured: true });
  const queryClient = renderGate();
  expect(await screen.findByText('Private conversations')).toBeInTheDocument();
  await act(async () => {
    await queryClient.invalidateQueries([QueryKeys.knowledgeAccess]);
  });
  expect(await screen.findByText('com_knowledge_access_denied')).toBeInTheDocument();
  expect(screen.queryByText('Private conversations')).not.toBeInTheDocument();
});

it('fails closed when access verification fails after a successful grant', async () => {
  jest
    .spyOn(dataService, 'getKnowledgeAccess')
    .mockResolvedValueOnce({ access: true, configured: true })
    .mockRejectedValueOnce(new Error('Unavailable'));
  const queryClient = renderGate();
  expect(await screen.findByText('Private conversations')).toBeInTheDocument();
  await act(async () => {
    await queryClient.invalidateQueries([QueryKeys.knowledgeAccess]);
  });
  expect(await screen.findByText('com_knowledge_access_failed')).toBeInTheDocument();
  expect(screen.queryByText('Private conversations')).not.toBeInTheDocument();
});

it('shows an unavailable state when the knowledge base is not configured', async () => {
  jest
    .spyOn(dataService, 'getKnowledgeAccess')
    .mockResolvedValue({ access: false, configured: false });
  renderGate();
  expect(await screen.findByText('com_knowledge_unavailable')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'com_nav_log_out' }));
  expect(mockLogout).toHaveBeenCalledWith('/login?redirect=false');
});

it('preserves the native chat when knowledge access is disabled', () => {
  const request = jest.spyOn(dataService, 'getKnowledgeAccess');
  mockUseGetStartupConfig.mockReturnValue({ data: { knowledge: { enabled: false } } });
  renderGate();
  expect(screen.getByText('Private conversations')).toBeInTheDocument();
  expect(request).not.toHaveBeenCalled();
});

it('does not query access before login', () => {
  const request = jest.spyOn(dataService, 'getKnowledgeAccess');
  mockUseAuthContext.mockReturnValue({ isAuthenticated: false });
  renderGate();
  expect(screen.queryByText('Private conversations')).not.toBeInTheDocument();
  expect(request).not.toHaveBeenCalled();
});
