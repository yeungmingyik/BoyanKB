import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import KnowledgeAsset from '../KnowledgeAsset';

const mockQuery = jest.fn();
jest.mock('../queries', () => ({
  useKnowledgeAsset: (...args: unknown[]) => mockQuery(...args),
  fetchKnowledgeAsset: jest.fn(),
}));
jest.mock('~/hooks', () => ({ useLocalize: () => (key: string) => key }));
jest.mock('@librechat/client', () => ({
  Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button {...props}>{children}</button>
  ),
  Spinner: () => <span />,
}));

const asset = {
  id: 'asset-1',
  mediaId: 'm1',
  name: 'Robot image',
  type: 'image' as const,
  mimeType: 'image/png',
  bytes: 10,
};
const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
);

describe('KnowledgeAsset', () => {
  beforeEach(() => {
    URL.createObjectURL = jest.fn(() => 'blob:local-image');
    URL.revokeObjectURL = jest.fn();
    mockQuery.mockReturnValue({ data: undefined, isLoading: false, isError: false });
  });

  it('does not automatically request SVG or other active document formats', () => {
    render(<KnowledgeAsset asset={{ ...asset, mimeType: 'image/svg+xml' }} />, { wrapper });
    expect(mockQuery).toHaveBeenCalledWith('asset-1', false);
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
  });

  it('only previews an authenticated image blob and revokes its URL after unmount', async () => {
    mockQuery.mockReturnValue({
      data: new Blob(['image'], { type: 'image/png' }),
      isLoading: false,
      isError: false,
    });
    const view = render(<KnowledgeAsset asset={asset} />, { wrapper });
    await waitFor(() => expect(screen.getByRole('img')).toHaveAttribute('src', 'blob:local-image'));
    expect(mockQuery).toHaveBeenCalledWith('asset-1', true);
    view.unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:local-image');
  });

  it('rejects a mismatched active-content response despite image metadata', () => {
    mockQuery.mockReturnValue({
      data: new Blob(['<html>'], { type: 'text/html' }),
      isLoading: false,
      isError: false,
    });
    render(<KnowledgeAsset asset={asset} />, { wrapper });
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
  });
});
