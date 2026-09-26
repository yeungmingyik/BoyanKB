import type { KnowledgeReadingBlock } from 'librechat-data-provider';
import type { FeishuBlock } from './feishu';

export type ExtractedMedia = {
  id: string;
  token: string;
  type: 'image' | 'file';
  name?: string;
  blockId: string;
};

export type ExtractionMissing = {
  blockId: string;
  blockType: number;
  reason: string;
};

export type ExtractedDocx = {
  blocks: KnowledgeReadingBlock[];
  text: string;
  media: ExtractedMedia[];
  missing: ExtractionMissing[];
  complete: boolean;
  parserVersion: string;
};

const parserVersion = 'docx-1';

function object(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function text(value: unknown): string {
  if (typeof value !== 'string') {
    return '';
  }
  return value
    .split('')
    .filter((character) => character.charCodeAt(0) >= 32 || '\t\n\r'.includes(character))
    .join('');
}

export function extractDocx(
  rawBlocks: FeishuBlock[],
  options: { maxDepth?: number } = {},
): ExtractedDocx {
  const maxDepth = options.maxDepth ?? 100;
  if (!Number.isSafeInteger(maxDepth) || maxDepth < 0 || maxDepth > 200) {
    throw new Error('DOCX_DEPTH_LIMIT_INVALID');
  }
  const blocks: KnowledgeReadingBlock[] = [];
  const media: ExtractedMedia[] = [];
  const missing: ExtractionMissing[] = [];
  const byId = new Map<string, FeishuBlock>();
  const publicIds = new Map<string, string>();
  const seen = new Set<string>();
  const active = new Set<string>();
  const mediaIds = new Map<string, string>();
  const missingKeys = new Set<string>();
  let generatedId = 0;

  const id = (source: string) => {
    if (!publicIds.has(source)) {
      publicIds.set(source, `b${++generatedId}`);
    }
    return publicIds.get(source)!;
  };

  const mark = (block: FeishuBlock, reason: string) => {
    const item = { blockId: id(block.block_id), blockType: block.block_type, reason };
    const key = `${item.blockId}:${reason}`;
    if (!missingKeys.has(key)) {
      missingKeys.add(key);
      missing.push(item);
    }
  };

  const addMedia = (block: FeishuBlock, token: unknown, type: 'image' | 'file', name?: unknown) => {
    if (type === 'file') {
      mark(block, 'attachment_extraction_unavailable');
    }
    if (typeof token !== 'string' || !/^[A-Za-z0-9_-]{1,256}$/.test(token)) {
      mark(block, 'invalid_media');
      return undefined;
    }
    const key = `${type}:${token}`;
    let mediaId = mediaIds.get(key);
    if (!mediaId) {
      mediaId = `m${media.length + 1}`;
      mediaIds.set(key, mediaId);
      media.push({
        id: mediaId,
        token,
        type,
        name: text(name) || undefined,
        blockId: id(block.block_id),
      });
    }
    return mediaId;
  };

  const readText = (block: FeishuBlock, value: unknown) => {
    const result: { text: string; attachments: KnowledgeReadingBlock[] } = {
      text: '',
      attachments: [],
    };
    const content = object(value);
    if (!content || !Array.isArray(content.elements)) {
      mark(block, 'invalid_text');
      return result;
    }
    for (const value of content.elements) {
      const element = object(value);
      if (!element) {
        mark(block, 'unsupported_element');
        continue;
      }
      const run = object(element.text_run);
      const equation = object(element.equation);
      const mention = object(element.mention_doc);
      const file = object(element.file);
      if (run && typeof run.content === 'string') {
        result.text += text(run.content);
      } else if (equation && typeof equation.content === 'string') {
        result.text += text(equation.content);
      } else if (mention && typeof mention.title === 'string') {
        result.text += text(mention.title);
      } else if (file) {
        const mediaId = addMedia(block, file.file_token, 'file');
        if (mediaId) {
          result.attachments.push({ id: `b${++generatedId}`, type: 'file', text: '', mediaId });
        }
      } else {
        mark(block, 'unsupported_element');
      }
    }
    return result;
  };

  for (const block of rawBlocks) {
    if (
      !block ||
      typeof block.block_id !== 'string' ||
      !block.block_id ||
      !Number.isSafeInteger(block.block_type)
    ) {
      missing.push({ blockId: `b${++generatedId}`, blockType: -1, reason: 'invalid_block' });
      continue;
    }
    id(block.block_id);
    if (byId.has(block.block_id)) {
      mark(block, 'duplicate_block');
      continue;
    }
    byId.set(block.block_id, block);
  }

  const visit = (block: FeishuBlock, depth: number): KnowledgeReadingBlock | undefined => {
    if (active.has(block.block_id)) {
      mark(block, 'cycle');
      return undefined;
    }
    if (seen.has(block.block_id)) {
      mark(block, 'duplicate_reference');
      return undefined;
    }
    if (depth > maxDepth) {
      mark(block, 'depth_limit');
      return undefined;
    }
    active.add(block.block_id);
    seen.add(block.block_id);
    const output: KnowledgeReadingBlock = { id: id(block.block_id), type: 'unsupported', text: '' };
    let textKey: string | undefined;
    if (block.block_type === 1) {
      output.type = 'section';
      textKey = 'page';
    } else if (block.block_type === 2) {
      output.type = 'paragraph';
      textKey = 'text';
    } else if (block.block_type >= 3 && block.block_type <= 11) {
      output.type = 'heading';
      output.level = block.block_type - 2;
      textKey = `heading${output.level}`;
    } else if (block.block_type === 12 || block.block_type === 13) {
      output.type = block.block_type === 12 ? 'bullet' : 'ordered';
      textKey = output.type;
    } else if (block.block_type === 19) {
      output.type = 'callout';
    } else if (block.block_type === 34) {
      output.type = 'quote';
    } else if (block.block_type === 31) {
      output.type = 'table';
    } else if (block.block_type === 32) {
      output.type = 'cell';
    } else if (block.block_type === 23 || block.block_type === 27) {
      output.type = block.block_type === 23 ? 'file' : 'image';
      const source = object(block[output.type]);
      output.text = text(source?.name) || text(object(source?.caption)?.content);
      output.mediaId = addMedia(block, source?.token, output.type, source?.name);
    } else {
      mark(block, 'unsupported_block');
    }

    let attachments: KnowledgeReadingBlock[] = [];
    if (textKey) {
      const content = readText(block, block[textKey]);
      output.text = block.block_type === 1 ? '' : content.text;
      attachments = content.attachments;
    }

    const table = object(block.table);
    const childIds = block.block_type === 31 ? table?.cells : block.children;
    const children: KnowledgeReadingBlock[] = [];
    if (childIds != null && !Array.isArray(childIds)) {
      mark(block, 'invalid_children');
    } else if (Array.isArray(childIds)) {
      for (const childId of childIds) {
        const child = typeof childId === 'string' ? byId.get(childId) : undefined;
        if (!child) {
          mark(block, 'missing_child');
          continue;
        }
        if (child.parent_id && child.parent_id !== block.block_id) {
          mark(block, 'parent_mismatch');
        }
        const parsed = visit(child, depth + 1);
        if (parsed) {
          children.push(parsed);
        }
      }
    }
    if (block.block_type === 31) {
      const properties = object(table?.property);
      const columns = properties?.column_size;
      const rows = properties?.row_size;
      const merges = properties?.merge_info;
      if (
        merges != null &&
        (!Array.isArray(merges) ||
          merges.some((value) => {
            const merge = object(value);
            return !merge || merge.row_span !== 1 || merge.col_span !== 1;
          }))
      ) {
        mark(block, 'merged_table_unavailable');
      }
      if (
        typeof columns !== 'number' ||
        typeof rows !== 'number' ||
        !Number.isSafeInteger(columns) ||
        !Number.isSafeInteger(rows) ||
        columns < 1 ||
        rows < 1 ||
        columns * rows !== children.length ||
        children.some((child) => child.type !== 'cell')
      ) {
        mark(block, 'invalid_table');
        output.children = children;
      } else {
        output.children = [];
        for (let index = 0; index < children.length; index += columns) {
          output.children.push({
            id: `b${++generatedId}`,
            type: 'row',
            text: '',
            children: children.slice(index, index + columns),
          });
        }
      }
    } else if (children.length || attachments.length) {
      output.children = [...attachments, ...children];
    }
    active.delete(block.block_id);
    return output;
  };

  const roots = Array.from(byId.values()).filter((block) => block.block_type === 1);
  if (roots.length !== 1) {
    missing.push({ blockId: 'document', blockType: 1, reason: 'invalid_root' });
  }
  for (const root of roots) {
    const parsed = visit(root, 0);
    if (parsed) {
      blocks.push(parsed);
    }
  }
  for (const block of byId.values()) {
    if (!seen.has(block.block_id)) {
      mark(block, 'orphan_block');
      const parsed = visit(block, 0);
      if (parsed) {
        blocks.push(parsed);
      }
    }
  }
  const collectText = (items: KnowledgeReadingBlock[]): string =>
    items
      .map((block) =>
        [block.text, block.children ? collectText(block.children) : ''].filter(Boolean).join('\n'),
      )
      .filter(Boolean)
      .join('\n');
  return {
    blocks,
    text: collectText(blocks),
    media,
    missing,
    complete: missing.length === 0,
    parserVersion,
  };
}
