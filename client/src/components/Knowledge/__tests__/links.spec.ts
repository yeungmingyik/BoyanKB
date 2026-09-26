import { isKnowledgeReferenceHref, knowledgeHashAnchor, knowledgeReferenceHref } from '../links';

describe('knowledge references', () => {
  it('builds a canonical version and block reference from public ids', () => {
    const href = knowledgeReferenceHref({
      documentId: 'doc-1',
      revisionId: 'rev-2',
      blockId: 'b3',
    });
    expect(href).toBe('/knowledge/documents/doc-1/revisions/rev-2#block-b3');
    expect(isKnowledgeReferenceHref(href!)).toBe(true);
  });

  it.each([
    'https://outside.example/knowledge/documents/doc-1/revisions/rev-2#block-b3',
    '//outside.example/knowledge/documents/doc-1/revisions/rev-2#block-b3',
    '/knowledge/documents/doc-1/revisions/rev-2?redirect=outside#block-b3',
    '/knowledge/documents/doc-1/revisions/../../admin#block-b3',
    '/knowledge/documents/doc-1/revisions/%2e%2e#block-b3',
    '/knowledge/documents/doc-1/revisions/rev-2#block-b3/extra',
  ])('does not treat an unsafe or noncanonical address as an internal reference: %s', (href) => {
    expect(isKnowledgeReferenceHref(href)).toBe(false);
  });

  it('rejects source addresses in ids and arbitrary hash selectors', () => {
    expect(
      knowledgeReferenceHref({
        documentId: 'https://outside.example',
        revisionId: 'rev-1',
        blockId: 'b1',
      }),
    ).toBeUndefined();
    expect(knowledgeHashAnchor('#block-b1, body')).toBeUndefined();
    expect(knowledgeHashAnchor('#block-b1')).toBe('block-b1');
  });
});
