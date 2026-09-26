import { MemoryRouter, useLocation } from 'react-router-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import KnowledgeSearch from '../KnowledgeSearch';

const mockQuery = jest.fn();
const mockRestart = jest.fn();
const mockMore = jest.fn();
jest.mock('../queries', () => ({
  knowledgeSearchQueryLimit: 1000,
  useKnowledgeSearch: (...args: unknown[]) => mockQuery(...args),
}));
jest.mock('~/hooks', () => ({ useLocalize: () => (key: string) => key }));
jest.mock('@librechat/client', () => ({
  Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button {...props}>{children}</button>
  ),
  Input: (props: React.InputHTMLAttributes<HTMLInputElement>) => <input {...props} />,
  Spinner: () => <span />,
}));
jest.mock('../KnowledgeTree', () => ({
  __esModule: true,
  default: ({ onSelectNode }: { onSelectNode: (node: { id: string; title: string }) => void }) => (
    <button type="button" onClick={() => onSelectNode({ id: 'node-1', title: 'Robotics' })}>
      {'Robotics scope'}
    </button>
  ),
}));

const hit = {
  documentId: 'doc-1',
  revisionId: 'rev-2',
  blockId: 'b3',
  title: 'Course guide',
  snippet: '<img src="https://outside.example/image">Robot assembly',
  href: 'https://outside.example/source',
  score: 1,
  matchedBy: ['keyword'],
};
const ready = {
  data: { pages: [{ items: [hit], sourceStatus: 'ready', snapshotId: 's1' }] },
  isError: false,
  isLoading: false,
  isFetching: false,
  restart: mockRestart,
  fetchNextPage: mockMore,
  hasNextPage: false,
};

function Location() {
  const location = useLocation();
  return (
    <output data-testid="location">
      {location.pathname}
      {location.search}
      {location.hash}
    </output>
  );
}

function setup(path = '/knowledge/search?q=robot&mode=keyword') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <KnowledgeSearch />
      <Location />
    </MemoryRouter>,
  );
}

describe('KnowledgeSearch', () => {
  beforeEach(() => mockQuery.mockReturnValue(ready));

  it('requires a nonempty query before submitting and preserves modes in the URL', () => {
    setup('/knowledge/search');
    expect(mockQuery).toHaveBeenLastCalledWith(undefined);
    expect(screen.getByRole('button', { name: 'com_ui_search' })).toBeDisabled();
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: '  robot  ' } });
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'semantic' } });
    fireEvent.submit(screen.getByRole('search'));
    expect(mockQuery).toHaveBeenLastCalledWith({
      query: 'robot',
      mode: 'semantic',
      directoryId: undefined,
      limit: 10,
    });
  });

  it('selects the internal directory id and can restore the whole-space scope', () => {
    setup();
    fireEvent.click(screen.getByText('com_knowledge_search_scope', { exact: false }));
    fireEvent.click(screen.getByRole('button', { name: 'Robotics scope' }));
    expect(mockQuery).toHaveBeenLastCalledWith({
      query: 'robot',
      mode: 'keyword',
      directoryId: 'node-1',
      limit: 10,
    });
    expect(screen.getByTestId('location')).toHaveTextContent('directory=node-1');
    fireEvent.click(screen.getByText('com_knowledge_search_scope', { exact: false }));
    fireEvent.click(screen.getByRole('button', { name: 'com_knowledge_search_all' }));
    expect(screen.getByTestId('location')).not.toHaveTextContent('directory=');
  });

  it('renders snippets as text and uses a versioned internal block link instead of an supplied address', () => {
    const view = setup();
    expect(screen.getByRole('link', { name: 'Course guide' })).toHaveAttribute(
      'href',
      '/knowledge/documents/doc-1/revisions/rev-2#block-b3',
    );
    expect(screen.getByText(hit.snippet)).toBeInTheDocument();
    expect(view.container.querySelector('img')).toBeNull();
    fireEvent.click(screen.getByRole('link', { name: 'Course guide' }));
    expect(screen.getByTestId('location')).toHaveTextContent(
      '/knowledge/documents/doc-1/revisions/rev-2#block-b3',
    );
  });

  it('hides previous results on permission failure', () => {
    const view = setup();
    mockQuery.mockReturnValue({
      ...ready,
      isError: true,
      error: { isAxiosError: true, response: { status: 403 } },
    });
    view.rerender(
      <MemoryRouter>
        <KnowledgeSearch />
      </MemoryRouter>,
    );
    expect(screen.queryByText('Course guide')).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('com_knowledge_access_denied');
  });

  it('restarts an expired result snapshot instead of appending stale pages', () => {
    mockQuery.mockReturnValue({
      ...ready,
      isError: true,
      error: { response: { status: 409, data: { code: 'KNOWLEDGE_SEARCH_SNAPSHOT_CHANGED' } } },
    });
    setup();
    expect(screen.queryByText('Course guide')).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('com_knowledge_search_changed');
    fireEvent.click(screen.getByRole('button', { name: 'com_ui_retry' }));
    expect(mockRestart).toHaveBeenCalledTimes(1);
    expect(mockMore).not.toHaveBeenCalled();
  });

  it('shows loading and empty responses without displaying stale results', () => {
    mockQuery.mockReturnValue({ ...ready, isLoading: true });
    const view = setup();
    expect(screen.queryByText('Course guide')).not.toBeInTheDocument();
    expect(screen.getByText('com_ui_loading')).toBeInTheDocument();
    mockQuery.mockReturnValue({ ...ready, data: { pages: [{ items: [] }] } });
    view.rerender(
      <MemoryRouter>
        <KnowledgeSearch />
      </MemoryRouter>,
    );
    expect(screen.getByText('com_knowledge_search_empty')).toBeInTheDocument();
  });

  it('continues only when another results page is available', () => {
    mockQuery.mockReturnValue({ ...ready, hasNextPage: true });
    setup();
    fireEvent.click(screen.getByRole('button', { name: 'com_ui_load_more' }));
    expect(mockMore).toHaveBeenCalledTimes(1);
  });

  it('rejects an oversized query from a direct URL', () => {
    setup(`/knowledge/search?q=${'a'.repeat(1001)}`);
    expect(screen.getByRole('alert')).toHaveTextContent('com_knowledge_search_invalid');
    expect(screen.queryByText('Course guide')).not.toBeInTheDocument();
  });
});
