import { MemoryRouter } from 'react-router-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import KnowledgeTree from '../KnowledgeTree';

const mockQuery = jest.fn();
const mockFetchNextPage = jest.fn();
jest.mock('../queries', () => ({ useKnowledgeTree: (parentId?: string) => mockQuery(parentId) }));
jest.mock('~/hooks', () => ({ useLocalize: () => (key: string) => key }));
jest.mock('~/utils', () => ({ cn: (...values: unknown[]) => values.filter(Boolean).join(' ') }));
jest.mock('@librechat/client', () => ({
  Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button {...props}>{children}</button>
  ),
  Spinner: () => <span />,
}));

const node = {
  id: 'node-1',
  parentId: null,
  documentId: 'doc-1',
  title: 'Robotics',
  objectType: 'docx',
  status: 'published',
  readable: true,
  hasChildren: true,
  order: 0,
};
const root = {
  data: { pages: [{ items: [node], nextCursor: 'next-page', sourceStatus: 'ready' }] },
  isLoading: false,
  isError: false,
  refetch: jest.fn(),
  hasNextPage: true,
  fetchNextPage: mockFetchNextPage,
};

describe('KnowledgeTree', () => {
  beforeEach(() => {
    mockQuery.mockImplementation((parentId) =>
      parentId
        ? {
            ...root,
            hasNextPage: false,
            data: {
              pages: [
                {
                  items: [
                    {
                      ...node,
                      id: 'node-2',
                      documentId: 'doc-2',
                      title: 'Drone activity',
                      hasChildren: false,
                    },
                  ],
                  sourceStatus: 'ready',
                },
              ],
            },
          }
        : root,
    );
  });

  it('loads child nodes only after their directory is expanded', () => {
    render(
      <MemoryRouter>
        <KnowledgeTree />
      </MemoryRouter>,
    );
    expect(mockQuery).not.toHaveBeenCalledWith('node-1');
    fireEvent.click(screen.getByRole('button', { name: 'com_knowledge_expand Robotics' }));
    expect(mockQuery).toHaveBeenCalledWith('node-1');
    expect(screen.getByRole('link', { name: 'Drone activity' })).toHaveAttribute(
      'href',
      '/knowledge/documents/doc-2',
    );
    fireEvent.click(screen.getByRole('button', { name: 'com_knowledge_collapse Robotics' }));
    expect(screen.queryByRole('link', { name: 'Drone activity' })).not.toBeInTheDocument();
  });

  it('continues the cursor instead of dropping subsequent pages', () => {
    render(
      <MemoryRouter>
        <KnowledgeTree />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'com_ui_load_more' }));
    expect(mockFetchNextPage).toHaveBeenCalledTimes(1);
  });

  it('keeps unsupported entries visible without a reading link', () => {
    mockQuery.mockReturnValue({
      ...root,
      data: {
        pages: [
          {
            items: [{ ...node, readable: false, status: 'unsupported', hasChildren: false }],
            sourceStatus: 'ready',
          },
        ],
      },
    });
    render(
      <MemoryRouter>
        <KnowledgeTree />
      </MemoryRouter>,
    );
    expect(screen.getByText('Robotics')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Robotics' })).not.toBeInTheDocument();
    expect(screen.getByText('com_knowledge_status_unsupported')).toBeInTheDocument();
  });

  it('hides old entries when source access is paused', () => {
    mockQuery.mockReturnValue({
      ...root,
      data: { pages: [{ items: [node], sourceStatus: 'paused' }] },
    });
    render(
      <MemoryRouter>
        <KnowledgeTree />
      </MemoryRouter>,
    );
    expect(screen.queryByRole('link', { name: 'Robotics' })).not.toBeInTheDocument();
    expect(screen.getByText('com_knowledge_source_unavailable')).toBeInTheDocument();
  });
});
