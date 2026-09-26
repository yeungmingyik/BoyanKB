type Output = Record<string, unknown>;

function record(value: unknown): Output | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Output)
    : undefined;
}

function resume(value: unknown): Output {
  const source = record(value) ?? {};
  const result: Output = { runSteps: [], aggregatedContent: [] };
  for (const key of [
    'conversationId',
    'userMessage',
    'responseMessageId',
    'sender',
    'model',
    'iconURL',
    'isRegenerate',
    'createdAt',
    'collectedUsage',
    'contextUsage',
    'pendingSteers',
  ]) {
    if (source[key] !== undefined) {
      result[key] = source[key];
    }
  }
  return result;
}

export function sanitizeKnowledgeResponse(value: unknown): Output {
  const response = record(value) ?? {};
  const knowledge = record(record(response.metadata)?.knowledge);
  if (knowledge?.verified === true) {
    return response;
  }
  const result: Output = {
    text: '',
    content: [],
    metadata: { knowledge: { verified: false, citations: [] } },
  };
  for (const key of [
    'messageId',
    'parentMessageId',
    'conversationId',
    'sender',
    'endpoint',
    'model',
    'createdAt',
    'updatedAt',
    'unfinished',
    'isCreatedByUser',
    'tokenCount',
  ]) {
    if (response[key] !== undefined) {
      result[key] = response[key];
    }
  }
  if (response.error) {
    result.error = true;
  }
  return result;
}

export function filterKnowledgeStreamEvent(value: unknown, terminal = false): Output | undefined {
  const event = record(value);
  if (!event) {
    return terminal ? { final: true } : undefined;
  }
  if (terminal || event.final === true || event.responseMessage !== undefined) {
    const result: Output = { final: true };
    for (const key of [
      'conversation',
      'title',
      'requestMessage',
      'pendingSteers',
      'generationProtocolVersion',
      'reconcile',
      'reconcileReason',
      'terminalStatus',
      'generationCreatedAt',
    ]) {
      if (event[key] !== undefined) {
        result[key] = event[key];
      }
    }
    if (event.responseMessage !== undefined) {
      result.responseMessage = sanitizeKnowledgeResponse(event.responseMessage);
    }
    if (event.error) {
      result.error = 'KNOWLEDGE_GENERATION_FAILED';
    }
    return result;
  }
  if (event.sync === true) {
    return {
      sync: true,
      resumeState: resume(event.resumeState),
      pendingEvents: [],
    };
  }
  if (event.created === true) {
    return {
      created: true,
      message: event.message,
      ...(event.responseMessageId ? { responseMessageId: event.responseMessageId } : {}),
    };
  }
  if (event.event === 'on_token_usage') {
    return event;
  }
  if (event.error) {
    return { error: 'KNOWLEDGE_GENERATION_FAILED' };
  }
  return undefined;
}

export function filterKnowledgeStreamStatus(value: Output): Output {
  return {
    ...value,
    aggregatedContent: [],
    resumeState: value.resumeState ? resume(value.resumeState) : undefined,
    pendingEvents: [],
    pendingAction: undefined,
  };
}
