import type { FeishuBlock } from './feishu';
import { extractDocx } from './extract';

function paragraph(id: string, content: string, parent = 'source_root'): FeishuBlock {
  return {
    block_id: id,
    block_type: 2,
    parent_id: parent,
    text: { elements: [{ text_run: { content } }] },
  };
}

function root(children: string[]): FeishuBlock {
  return {
    block_id: 'source_root',
    block_type: 1,
    children,
    page: { elements: [{ text_run: { content: 'Synthetic title' } }] },
  };
}

describe('Docx extraction', () => {
  it('preserves the source child order independently of page order', () => {
    const result = extractDocx([
      root(['second', 'first']),
      paragraph('first', 'First'),
      paragraph('second', 'Second'),
    ]);
    expect(result.complete).toBe(true);
    expect(result.text).toBe('Second\nFirst');
    expect(result.blocks[0].text).toBe('');
    expect(result.blocks[0].children?.map((block) => block.text)).toEqual(['Second', 'First']);
    expect(JSON.stringify(result.blocks)).not.toMatch(/source_root|second|first/);
  });

  it('extracts headings, ordered and bullet lists, callouts and quote containers', () => {
    const children: FeishuBlock[] = Array.from({ length: 9 }, (_, index) => ({
      block_id: `heading_${index + 1}`,
      block_type: index + 3,
      parent_id: 'source_root',
      [`heading${index + 1}`]: { elements: [{ text_run: { content: `Heading ${index + 1}` } }] },
    }));
    children.push(
      {
        block_id: 'bullet',
        block_type: 12,
        parent_id: 'source_root',
        bullet: { elements: [{ text_run: { content: 'Bullet' } }] },
      },
      {
        block_id: 'ordered',
        block_type: 13,
        parent_id: 'source_root',
        ordered: { elements: [{ text_run: { content: 'Ordered' } }] },
      },
      {
        block_id: 'callout',
        block_type: 19,
        parent_id: 'source_root',
        children: ['callout_text'],
        callout: {},
      },
      {
        block_id: 'quote',
        block_type: 34,
        parent_id: 'source_root',
        children: ['quote_text'],
        quote_container: {},
      },
    );
    const result = extractDocx([
      root(children.map((block) => block.block_id)),
      ...children,
      paragraph('callout_text', 'Callout', 'callout'),
      paragraph('quote_text', 'Quote', 'quote'),
    ]);
    expect(result.complete).toBe(true);
    expect(result.blocks[0].children?.map((block) => block.type)).toEqual([
      ...Array(9).fill('heading'),
      'bullet',
      'ordered',
      'callout',
      'quote',
    ]);
    expect(result.blocks[0].children?.slice(0, 9).map((block) => block.level)).toEqual([
      1, 2, 3, 4, 5, 6, 7, 8, 9,
    ]);
  });

  it('reconstructs table rows and nested cells using table cell order', () => {
    const result = extractDocx([
      root(['table']),
      {
        block_id: 'table',
        block_type: 31,
        parent_id: 'source_root',
        table: { cells: ['cell_a', 'cell_b'], property: { row_size: 1, column_size: 2 } },
      },
      {
        block_id: 'cell_b',
        block_type: 32,
        parent_id: 'table',
        children: ['text_b'],
        table_cell: {},
      },
      paragraph('text_b', 'B', 'cell_b'),
      {
        block_id: 'cell_a',
        block_type: 32,
        parent_id: 'table',
        children: ['text_a'],
        table_cell: {},
      },
      paragraph('text_a', 'A', 'cell_a'),
    ]);
    expect(result.complete).toBe(true);
    const table = result.blocks[0].children?.[0];
    expect(table?.type).toBe('table');
    expect(table?.children?.[0].type).toBe('row');
    expect(table?.children?.[0].children?.map((cell) => cell.children?.[0].text)).toEqual([
      'A',
      'B',
    ]);
  });

  it('marks a malformed table incomplete', () => {
    const result = extractDocx([
      root(['table']),
      {
        block_id: 'table',
        block_type: 31,
        table: { cells: [], property: { row_size: 1, column_size: 2 } },
      },
    ]);
    expect(result.complete).toBe(false);
    expect(result.missing).toContainEqual(expect.objectContaining({ reason: 'invalid_table' }));
  });

  it('records merged table cells rather than silently flattening their layout', () => {
    const result = extractDocx([
      root(['table']),
      {
        block_id: 'table',
        block_type: 31,
        table: {
          cells: ['cell'],
          property: {
            row_size: 1,
            column_size: 1,
            merge_info: [{ row_span: 2, col_span: 1 }],
          },
        },
      },
      { block_id: 'cell', block_type: 32, parent_id: 'table', children: [], table_cell: {} },
    ]);
    expect(result.complete).toBe(false);
    expect(result.missing).toContainEqual(
      expect.objectContaining({ reason: 'merged_table_unavailable' }),
    );
  });

  it('keeps plain text literal and discards source URLs, style HTML and metadata', () => {
    const result = extractDocx([
      root(['text']),
      {
        block_id: 'text',
        block_type: 2,
        parent_id: 'source_root',
        secret: 'never_publish',
        text: {
          elements: [
            {
              text_run: {
                content: '<script>literal</script>',
                text_element_style: { link: { url: 'http://127.0.0.1/private' } },
              },
            },
            { equation: { content: 'x+y' } },
            { mention_doc: { token: 'source_private_token', title: 'Linked resource' } },
          ],
        },
      },
    ]);
    expect(result.complete).toBe(true);
    expect(result.text).toContain('<script>literal</script>x+yLinked resource');
    expect(JSON.stringify(result)).not.toMatch(/127\.0\.0\.1|source_private_token|never_publish/);
  });

  it.each([18, 26, 30, 43, 49, 50, 999])(
    'marks embedded type %s missing instead of producing empty success',
    (blockType) => {
      const result = extractDocx([
        root(['embed']),
        { block_id: 'embed', block_type: blockType, token: 'private_token' },
      ]);
      expect(result.complete).toBe(false);
      expect(result.blocks[0].children?.[0].type).toBe('unsupported');
      expect(result.missing).toContainEqual(
        expect.objectContaining({ blockType, reason: 'unsupported_block' }),
      );
      expect(JSON.stringify(result)).not.toContain('private_token');
    },
  );

  it('extracts local image relationships without publishing source tokens', () => {
    const result = extractDocx([
      root(['image']),
      {
        block_id: 'image',
        block_type: 27,
        parent_id: 'source_root',
        image: { token: 'private_media_token', caption: { content: 'Caption' } },
      },
    ]);
    expect(result.complete).toBe(true);
    expect(result.media).toEqual([
      { id: 'm1', token: 'private_media_token', type: 'image', name: undefined, blockId: 'b2' },
    ]);
    expect(result.blocks[0].children?.[0]).toMatchObject({
      type: 'image',
      text: 'Caption',
      mediaId: 'm1',
    });
    expect(JSON.stringify(result.blocks)).not.toContain('private_media_token');
  });

  it('records standalone and inline file relations as incomplete until content extraction exists', () => {
    const result = extractDocx([
      root(['file', 'inline']),
      { block_id: 'file', block_type: 23, file: { token: 'file_token', name: 'Synthetic.pdf' } },
      {
        block_id: 'inline',
        block_type: 2,
        text: { elements: [{ file: { file_token: 'file_token', source_block_id: 'file' } }] },
      },
    ]);
    expect(result.complete).toBe(false);
    expect(result.media).toHaveLength(1);
    expect(
      result.missing.filter((item) => item.reason === 'attachment_extraction_unavailable'),
    ).toHaveLength(2);
    expect(JSON.stringify(result.blocks)).not.toContain('file_token');
  });

  it('records missing children and unknown inline objects', () => {
    const result = extractDocx([
      root(['text', 'missing']),
      { block_id: 'text', block_type: 2, text: { elements: [{ unknown: { data: 'private' } }] } },
    ]);
    expect(result.complete).toBe(false);
    expect(result.missing.map((item) => item.reason)).toEqual(
      expect.arrayContaining(['missing_child', 'unsupported_element']),
    );
    expect(JSON.stringify(result)).not.toContain('private');
  });

  it('detects cycles, duplicate references and unattached blocks', () => {
    const result = extractDocx([
      root(['one', 'one']),
      { ...paragraph('one', 'One'), children: ['source_root'] },
      paragraph('orphan', 'Unattached'),
    ]);
    expect(result.complete).toBe(false);
    expect(result.missing.map((item) => item.reason)).toEqual(
      expect.arrayContaining(['cycle', 'duplicate_reference', 'orphan_block']),
    );
  });

  it('detects duplicate blocks and parent conflicts', () => {
    const result = extractDocx([
      root(['one']),
      paragraph('one', 'One', 'other'),
      paragraph('one', 'Duplicate'),
    ]);
    expect(result.complete).toBe(false);
    expect(result.missing.map((item) => item.reason)).toEqual(
      expect.arrayContaining(['duplicate_block', 'parent_mismatch']),
    );
  });

  it('bounds depth and distinguishes an empty document from an absent root', () => {
    expect(extractDocx([{ ...root([]), page: { elements: [] } }]).complete).toBe(true);
    expect(extractDocx([])).toMatchObject({
      complete: false,
      missing: [{ blockId: 'document', blockType: 1, reason: 'invalid_root' }],
    });
    const deep = extractDocx(
      [
        root(['one']),
        { ...paragraph('one', 'One'), children: ['two'] },
        paragraph('two', 'Two', 'one'),
      ],
      { maxDepth: 1 },
    );
    expect(deep.complete).toBe(false);
    expect(deep.missing).toContainEqual(expect.objectContaining({ reason: 'depth_limit' }));
  });
});
