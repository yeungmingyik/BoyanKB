import { MemoryRouter, useLocation } from 'react-router-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { a as MarkdownAnchor } from '../MarkdownComponents';

jest.mock('recoil', () => ({ useRecoilValue: () => ({ id: 'user-1' }) }));
jest.mock('@librechat/client', () => ({ useToastContext: () => ({ showToast: jest.fn() }) }));
jest.mock('~/data-provider', () => ({ useFileDownload: () => ({ refetch: jest.fn() }) }));
jest.mock('~/hooks', () => ({ useLocalize: () => (key: string) => key }));
jest.mock('~/store', () => ({ __esModule: true, default: { user: 'user' } }));
jest.mock('~/utils', () => ({}));
jest.mock('~/Providers', () => ({}));
jest.mock('~/hooks/Roles/useHasAccess', () => ({ __esModule: true, default: jest.fn() }));
jest.mock('~/components/Messages/Content/Mermaid', () => ({
  __esModule: true,
  default: () => null,
}));
jest.mock('~/components/Messages/Content/CodeBlock', () => ({
  __esModule: true,
  default: () => null,
}));

function Location() {
  const location = useLocation();
  return (
    <output data-testid="location">
      {location.pathname}
      {location.hash}
    </output>
  );
}

describe('markdown knowledge links', () => {
  it('opens a canonical knowledge revision and block in the current application', () => {
    const href = '/knowledge/documents/doc-1/revisions/rev-2#block-b3';
    render(
      <MemoryRouter initialEntries={['/c/chat-1']}>
        <MarkdownAnchor href={href}>[1]</MarkdownAnchor>
        <Location />
      </MemoryRouter>,
    );
    const link = screen.getByRole('link', { name: '[1]' });
    expect(link).not.toHaveAttribute('target');
    fireEvent.click(link);
    expect(screen.getByTestId('location')).toHaveTextContent(href);
  });

  it.each([
    'https://outside.example/knowledge/documents/doc-1/revisions/rev-2#block-b3',
    '/knowledge/documents/doc-1/revisions/rev-2?redirect=outside#block-b3',
  ])('preserves native handling for a noncanonical link: %s', (href) => {
    render(
      <MemoryRouter>
        <MarkdownAnchor href={href}>{'Reference'}</MarkdownAnchor>
      </MemoryRouter>,
    );
    expect(screen.getByRole('link')).toHaveAttribute('target', '_blank');
    expect(screen.getByRole('link')).toHaveAttribute('href', href);
  });
});
