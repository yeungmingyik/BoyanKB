import { createElement } from 'react';
import type { KnowledgeAsset as Asset, KnowledgeReadingBlock } from 'librechat-data-provider';
import type { ReactNode } from 'react';
import { knowledgeBlockAnchor } from './links';
import KnowledgeAsset from './KnowledgeAsset';
import { useLocalize } from '~/hooks';

function ReadingBlock({
  block,
  assets,
  depth,
}: {
  block: KnowledgeReadingBlock;
  assets: Asset[];
  depth: number;
}) {
  const localize = useLocalize();
  const anchorProps = { id: knowledgeBlockAnchor(block.id), tabIndex: -1 };
  const anchorClass =
    'scroll-mt-6 rounded-sm target:bg-surface-secondary target:ring-2 target:ring-border-heavy focus:outline-none';
  if (depth > 40) {
    return <p>{localize('com_knowledge_content_unavailable')}</p>;
  }
  const nested = block.children?.length ? (
    <ReadingBlocks blocks={block.children} assets={assets} depth={depth + 1} />
  ) : null;
  const text = block.text ? (
    <span className="whitespace-pre-wrap break-words">{block.text}</span>
  ) : null;

  switch (block.type) {
    case 'section':
      return (
        <section {...anchorProps} className={anchorClass}>
          {text}
          {nested}
        </section>
      );
    case 'heading':
      return (
        <>
          {createElement(
            `h${Math.max(2, Math.min(6, (block.level ?? 1) + 1))}`,
            {
              ...anchorProps,
              className: `${anchorClass} mb-3 mt-6 font-semibold text-text-primary`,
            },
            text,
          )}
          {nested}
        </>
      );
    case 'quote':
    case 'callout':
      return (
        <blockquote
          {...anchorProps}
          className={`${anchorClass} my-4 border-l-4 border-border-heavy bg-surface-secondary p-4`}
        >
          {text}
          {nested}
        </blockquote>
      );
    case 'bullet':
    case 'ordered':
      return (
        <li {...anchorProps} className={anchorClass}>
          {text}
          {nested}
        </li>
      );
    case 'table':
      return (
        <div {...anchorProps} className={`${anchorClass} my-4 max-w-full overflow-x-auto`}>
          <table className="w-full border-collapse text-left text-sm">
            <tbody>{nested}</tbody>
          </table>
        </div>
      );
    case 'row':
      return (
        <tr {...anchorProps} className={anchorClass}>
          {nested}
        </tr>
      );
    case 'cell':
      return (
        <td
          {...anchorProps}
          className={`${anchorClass} min-w-28 border border-border-medium p-3 align-top`}
        >
          {text}
          {nested}
        </td>
      );
    case 'image':
    case 'file': {
      const asset = assets.find((item) => item.mediaId === block.mediaId);
      return (
        <div {...anchorProps} className={anchorClass}>
          {asset ? (
            <KnowledgeAsset asset={asset} caption={block.text} />
          ) : (
            <p className="my-3 text-text-secondary">
              {localize('com_knowledge_content_unavailable')}
            </p>
          )}
        </div>
      );
    }
    case 'unsupported':
      return (
        <p {...anchorProps} className={`${anchorClass} my-3 text-text-secondary`}>
          {localize('com_knowledge_content_unavailable')}
        </p>
      );
    default:
      return (
        <div {...anchorProps} className={`${anchorClass} my-3 leading-7`}>
          {text}
          {nested}
        </div>
      );
  }
}

export default function ReadingBlocks({
  blocks,
  assets,
  depth = 0,
}: {
  blocks: KnowledgeReadingBlock[];
  assets: Asset[];
  depth?: number;
}) {
  const elements: ReactNode[] = [];
  for (let index = 0; index < blocks.length; index++) {
    const block = blocks[index];
    if (block.type !== 'bullet' && block.type !== 'ordered') {
      elements.push(<ReadingBlock key={block.id} block={block} assets={assets} depth={depth} />);
      continue;
    }
    const items: ReactNode[] = [];
    let next = index;
    while (next < blocks.length && blocks[next].type === block.type) {
      items.push(
        <ReadingBlock key={blocks[next].id} block={blocks[next]} assets={assets} depth={depth} />,
      );
      next++;
    }
    elements.push(
      createElement(
        block.type === 'ordered' ? 'ol' : 'ul',
        {
          key: block.id,
          className: `my-2 pl-6 ${block.type === 'ordered' ? 'list-decimal' : 'list-disc'}`,
        },
        items,
      ),
    );
    index = next - 1;
  }
  return <>{elements}</>;
}
