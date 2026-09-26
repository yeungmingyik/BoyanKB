const publicIdPattern = /^[a-zA-Z0-9_-]{1,128}$/;
const referencePattern =
  /^\/knowledge\/documents\/[a-zA-Z0-9_-]{1,128}\/revisions\/[a-zA-Z0-9_-]{1,128}#block-[a-zA-Z0-9_-]{1,128}$/;

export function knowledgeBlockAnchor(blockId: string) {
  return publicIdPattern.test(blockId) ? `block-${blockId}` : undefined;
}

export function knowledgeReferenceHref(reference: {
  documentId: string;
  revisionId: string;
  blockId: string;
}) {
  const anchor = knowledgeBlockAnchor(reference.blockId);
  if (
    !anchor ||
    !publicIdPattern.test(reference.documentId) ||
    !publicIdPattern.test(reference.revisionId)
  ) {
    return undefined;
  }
  return `/knowledge/documents/${reference.documentId}/revisions/${reference.revisionId}#${anchor}`;
}

export function isKnowledgeReferenceHref(href: string) {
  return referencePattern.test(href);
}

export function knowledgeHashAnchor(hash: string) {
  return /^#block-[a-zA-Z0-9_-]{1,128}$/.test(hash) ? hash.slice(1) : undefined;
}
