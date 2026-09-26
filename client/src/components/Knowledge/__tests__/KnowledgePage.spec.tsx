import { MemoryRouter } from 'react-router-dom';
import { render, screen } from '@testing-library/react';
import KnowledgePage from '../KnowledgePage';

let mockRole = 'USER';
jest.mock('~/hooks', () => ({
  useLocalize: () => (key: string) => key,
  useAuthContext: () => ({ user: { id: 'user-1', role: mockRole } }),
}));
jest.mock('~/data-provider', () => ({
  useGetStartupConfig: () => ({ data: { knowledge: { enabled: true } } }),
}));
jest.mock('~/components/Chat/Menus/OpenSidebar', () => ({
  __esModule: true,
  default: () => <button>{'Navigation'}</button>,
}));
jest.mock('../SyncPanel', () => ({
  __esModule: true,
  default: () => <div data-testid="sync-management" />,
}));
jest.mock('../KnowledgeTree', () => ({
  __esModule: true,
  default: () => <div>{'Directory'}</div>,
}));
jest.mock('../KnowledgeReader', () => ({
  __esModule: true,
  default: ({ documentId, revisionId }: { documentId: string; revisionId?: string }) => (
    <div data-testid="reader">
      {documentId}:{revisionId}
    </div>
  ),
}));
jest.mock('~/utils', () => ({ cn: (...values: unknown[]) => values.filter(Boolean).join(' ') }));

describe('KnowledgePage', () => {
  beforeEach(() => {
    mockRole = 'USER';
  });

  it('never mounts sync management for a partner', () => {
    render(
      <MemoryRouter initialEntries={['/knowledge']}>
        <KnowledgePage />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId('sync-management')).not.toBeInTheDocument();
    expect(screen.getByText('Directory')).toBeInTheDocument();
  });

  it('provides sync management in the same page for administrators', () => {
    mockRole = 'ADMIN';
    render(
      <MemoryRouter initialEntries={['/knowledge']}>
        <KnowledgePage />
      </MemoryRouter>,
    );
    expect(screen.getByTestId('sync-management')).toBeInTheDocument();
  });

  it('opens an internal referenced revision', () => {
    render(
      <MemoryRouter initialEntries={['/knowledge/documents/doc-1/revisions/rev-2']}>
        <KnowledgePage />
      </MemoryRouter>,
    );
    expect(screen.getByTestId('reader')).toHaveTextContent('doc-1:rev-2');
    expect(screen.queryByTestId('sync-management')).not.toBeInTheDocument();
  });
});
