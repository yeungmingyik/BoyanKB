import { render, screen, within } from '@testing-library/react';
import type { KnowledgeAsset, KnowledgeReadingBlock } from 'librechat-data-provider';
import ReadingBlocks from '../ReadingBlocks';

jest.mock('~/hooks', () => ({ useLocalize: () => (key: string) => key }));
jest.mock('../KnowledgeAsset', () => ({
  __esModule: true,
  default: ({ asset }: { asset: KnowledgeAsset }) => <span data-testid="asset">{asset.id}</span>,
}));

describe('ReadingBlocks', () => {
  it('renders source markup as text without links, scripts, or remote images', () => {
    const text =
      '<img src="https://outside.example/image.png" onerror="alert(1)"><script>alert(1)</script>';
    const view = render(
      <ReadingBlocks blocks={[{ id: 'b1', type: 'paragraph', text }]} assets={[]} />,
    );
    expect(screen.getByText(text)).toBeInTheDocument();
    expect(view.container.querySelector('script, img, iframe, a')).toBeNull();
  });

  it('keeps consecutive ordered items in the same numbered list', () => {
    render(
      <ReadingBlocks
        blocks={[
          { id: 'b1', type: 'ordered', text: 'Build' },
          { id: 'b2', type: 'ordered', text: 'Test' },
          { id: 'b3', type: 'paragraph', text: 'Next activity' },
          { id: 'b4', type: 'ordered', text: 'Share' },
        ]}
        assets={[]}
      />,
    );
    const lists = screen.getAllByRole('list');
    expect(lists).toHaveLength(2);
    expect(within(lists[0]).getAllByRole('listitem')).toHaveLength(2);
    expect(within(lists[1]).getAllByRole('listitem')).toHaveLength(1);
  });

  it('keeps nested tables and cell text in reading order', () => {
    const blocks: KnowledgeReadingBlock[] = [
      {
        id: 'b1',
        type: 'table',
        text: '',
        children: [
          {
            id: 'b2',
            type: 'row',
            text: '',
            children: [
              { id: 'b3', type: 'cell', text: 'Course' },
              {
                id: 'b4',
                type: 'cell',
                text: '',
                children: [{ id: 'b5', type: 'paragraph', text: 'Robotics' }],
              },
            ],
          },
        ],
      },
    ];
    render(<ReadingBlocks blocks={blocks} assets={[]} />);
    const cells = within(screen.getByRole('table')).getAllByRole('cell');
    expect(cells.map((cell) => cell.textContent)).toEqual(['Course', 'Robotics']);
  });

  it('resolves media through the public asset mapping', () => {
    render(
      <ReadingBlocks
        blocks={[{ id: 'b1', type: 'image', text: '', mediaId: 'm1' }]}
        assets={[
          {
            id: 'asset-123',
            mediaId: 'm1',
            type: 'image',
            name: 'Robot',
            mimeType: 'image/png',
            bytes: 10,
          },
        ]}
      />,
    );
    expect(screen.getByTestId('asset')).toHaveTextContent('asset-123');
  });

  it('shows missing media without using its id as an external address', () => {
    const view = render(
      <ReadingBlocks
        blocks={[{ id: 'b1', type: 'image', text: '', mediaId: 'https://outside.example/image' }]}
        assets={[]}
      />,
    );
    expect(screen.getByText('com_knowledge_content_unavailable')).toBeInTheDocument();
    expect(view.container.querySelector('img, a')).toBeNull();
    expect(screen.queryByText('https://outside.example/image')).not.toBeInTheDocument();
  });
});
