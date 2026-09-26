import { MemoryRouter } from 'react-router-dom';
import { render, screen, waitFor } from '@testing-library/react';
import KnowledgeReader from '../KnowledgeReader';

const mockQuery = jest.fn();
jest.mock('../queries', () => ({
  useKnowledgeDocument: (...args: unknown[]) => mockQuery(...args),
}));
jest.mock('~/hooks', () => ({ useLocalize: () => (key: string) => key }));
jest.mock('@librechat/client', () => ({
  Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button {...props}>{children}</button>
  ),
  Spinner: () => <span />,
}));
jest.mock('../ReadingBlocks', () => ({
  __esModule: true,
  default: () => (
    <p id="block-b1" tabIndex={-1}>
      {'Private document body'}
    </p>
  ),
}));

const data = {
  id: 'doc-1',
  title: 'Course guide',
  status: 'published',
  revision: { id: 'rev-1', publishedAt: '2026-09-26T00:00:00Z' },
  blocks: [{ id: 'b1', type: 'paragraph', text: 'Private document body' }],
  assets: [],
};

describe('KnowledgeReader', () => {
  beforeEach(() =>
    mockQuery.mockReturnValue({ data, isError: false, isLoading: false, refetch: jest.fn() }),
  );

  it('hides a cached document after a denied refetch', () => {
    const view = render(
      <MemoryRouter>
        <KnowledgeReader documentId="doc-1" />
      </MemoryRouter>,
    );
    expect(screen.getByText('Private document body')).toBeInTheDocument();
    mockQuery.mockReturnValue({ data, isError: true, error: { status: 403 }, refetch: jest.fn() });
    view.rerender(
      <MemoryRouter>
        <KnowledgeReader documentId="doc-1" />
      </MemoryRouter>,
    );
    expect(screen.queryByText('Private document body')).not.toBeInTheDocument();
    expect(screen.queryByText('Course guide')).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('com_knowledge_access_denied');
  });

  it.each(['removed', 'inaccessible'])('does not render a %s document', (status) => {
    mockQuery.mockReturnValue({ data: { ...data, status }, isError: false, refetch: jest.fn() });
    render(
      <MemoryRouter>
        <KnowledgeReader documentId="doc-1" />
      </MemoryRouter>,
    );
    expect(screen.queryByText('Private document body')).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('com_knowledge_document_unavailable');
  });

  it('keeps earlier snapshots distinct from the latest version', () => {
    render(
      <MemoryRouter>
        <KnowledgeReader documentId="doc-1" revisionId="rev-1" />
      </MemoryRouter>,
    );
    expect(mockQuery).toHaveBeenCalledWith('doc-1', 'rev-1');
    expect(screen.getByRole('link', { name: 'com_knowledge_latest_revision' })).toHaveAttribute(
      'href',
      '/knowledge/documents/doc-1',
    );
  });

  it('focuses and scrolls the referenced block only after its snapshot has loaded', async () => {
    const scroll = jest.fn();
    const previousScroll = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = scroll;
    try {
      mockQuery.mockReturnValue({ isLoading: true, refetch: jest.fn() });
      const view = render(
        <MemoryRouter initialEntries={['/knowledge/documents/doc-1/revisions/rev-1#block-b1']}>
          <KnowledgeReader documentId="doc-1" revisionId="rev-1" />
        </MemoryRouter>,
      );
      expect(scroll).not.toHaveBeenCalled();
      mockQuery.mockReturnValue({ data, isLoading: false, isError: false, refetch: jest.fn() });
      view.rerender(
        <MemoryRouter>
          <KnowledgeReader documentId="doc-1" revisionId="rev-1" />
        </MemoryRouter>,
      );
      await waitFor(() => expect(screen.getByText('Private document body')).toHaveFocus());
      expect(scroll).toHaveBeenCalledWith({ block: 'center', behavior: 'auto' });
    } finally {
      HTMLElement.prototype.scrollIntoView = previousScroll;
    }
  });

  it('keeps the search scope when returning from a result', () => {
    render(
      <MemoryRouter
        initialEntries={[
          {
            pathname: '/knowledge/documents/doc-1/revisions/rev-1',
            state: { knowledgeSearch: '/knowledge/search?q=robot&directory=node-1' },
          },
        ]}
      >
        <KnowledgeReader documentId="doc-1" revisionId="rev-1" />
      </MemoryRouter>,
    );
    expect(screen.getByRole('link', { name: 'com_knowledge_search_results' })).toHaveAttribute(
      'href',
      '/knowledge/search?q=robot&directory=node-1',
    );
  });

  it('does not follow an external return address or focus an outside element', () => {
    const scroll = jest.fn();
    const outside = document.createElement('div');
    outside.id = 'block-outside';
    outside.scrollIntoView = scroll;
    document.body.appendChild(outside);
    try {
      render(
        <MemoryRouter
          initialEntries={[
            {
              pathname: '/knowledge/documents/doc-1',
              hash: '#block-outside',
              state: { knowledgeSearch: '//outside.example/knowledge/search' },
            },
          ]}
        >
          <KnowledgeReader documentId="doc-1" />
        </MemoryRouter>,
      );
      expect(screen.getByRole('link', { name: 'com_knowledge_directory' })).toHaveAttribute(
        'href',
        '/knowledge',
      );
      expect(scroll).not.toHaveBeenCalled();
    } finally {
      outside.remove();
    }
  });
});
