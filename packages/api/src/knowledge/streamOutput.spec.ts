import {
  filterKnowledgeStreamEvent,
  filterKnowledgeStreamStatus,
  sanitizeKnowledgeResponse,
} from './streamOutput';

describe('knowledge verified stream output', () => {
  it.each([
    { event: 'on_message_delta', data: { content: 'synthetic unverified answer' } },
    { event: 'on_run_step', data: { text: 'synthetic source snippet' } },
    { event: 'on_tool_end', data: { output: 'synthetic source snippet' } },
    { message: 'synthetic unverified answer' },
  ])('withholds nonterminal model and tool payloads %p', (event) => {
    expect(filterKnowledgeStreamEvent(event)).toBeUndefined();
  });

  it('removes unverified content from replay frames', () => {
    const result = filterKnowledgeStreamEvent({
      sync: true,
      resumeState: { runSteps: ['synthetic secret'], aggregatedContent: ['synthetic secret'] },
      pendingEvents: [{ text: 'synthetic secret' }],
    });
    expect(result).toEqual({
      sync: true,
      resumeState: { runSteps: [], aggregatedContent: [] },
      pendingEvents: [],
    });
  });

  it('preserves the created user message and usage events', () => {
    const message = { messageId: 'synthetic-user-message', text: 'User question' };
    expect(filterKnowledgeStreamEvent({ created: true, message })).toEqual({
      created: true,
      message,
    });
    const usage = { event: 'on_token_usage', data: { inputTokens: 8, outputTokens: 3 } };
    expect(filterKnowledgeStreamEvent(usage)).toEqual(usage);
  });

  it('retains the user and response identity required to reconnect an unfinished turn', () => {
    const userMessage = { messageId: 'synthetic-user', text: 'Question' };
    const result = filterKnowledgeStreamStatus({
      active: true,
      resumeState: {
        conversationId: 'synthetic-conversation',
        responseMessageId: 'synthetic-response',
        userMessage,
        aggregatedContent: ['synthetic unverified answer'],
        pendingAction: { text: 'synthetic unverified answer' },
        pendingOAuthPrompts: ['synthetic secret'],
      },
    });
    expect(result.resumeState).toEqual({
      conversationId: 'synthetic-conversation',
      responseMessageId: 'synthetic-response',
      userMessage,
      runSteps: [],
      aggregatedContent: [],
    });
  });

  it('allows only a server-verified response through the final frame', () => {
    const response = {
      messageId: 'synthetic-answer',
      text: 'Verified answer [1]',
      content: [{ type: 'text', text: 'Verified answer [1]' }],
      metadata: { knowledge: { verified: true, citations: [{ documentId: 'synthetic-doc' }] } },
    };
    expect(filterKnowledgeStreamEvent({ final: true, responseMessage: response })).toEqual({
      final: true,
      responseMessage: response,
    });
  });

  it.each([undefined, { verified: false }, { verified: 'true' }])(
    'turns a canceled or unverified response into an empty native shell %p',
    (knowledge) => {
      const result = filterKnowledgeStreamEvent({
        final: true,
        text: 'synthetic unverified answer',
        content: ['synthetic unverified answer'],
        responseMessage: {
          messageId: 'synthetic-answer',
          unfinished: true,
          text: 'synthetic unverified answer',
          content: ['synthetic unverified answer'],
          reasoning: 'synthetic unverified answer',
          metadata: { knowledge },
        },
      });
      expect(result?.responseMessage).toMatchObject({
        messageId: 'synthetic-answer',
        unfinished: true,
        text: '',
        content: [],
      });
      expect(JSON.stringify(result)).not.toContain('synthetic unverified answer');
    },
  );

  it('hides active and inactive status buffers until the verified history is fetched', () => {
    const result = filterKnowledgeStreamStatus({
      active: true,
      streamId: 'synthetic-stream',
      aggregatedContent: ['synthetic unverified answer'],
      resumeState: { runSteps: ['synthetic unverified answer'] },
      pendingAction: { text: 'synthetic unverified answer' },
    });
    expect(result.streamId).toBe('synthetic-stream');
    expect(JSON.stringify(result)).not.toContain('synthetic unverified answer');
  });

  it('never forwards raw error details or unverified response metadata', () => {
    expect(filterKnowledgeStreamEvent({ error: 'synthetic secret' })).toEqual({
      error: 'KNOWLEDGE_GENERATION_FAILED',
    });
    expect(sanitizeKnowledgeResponse({ metadata: { source: 'synthetic secret' } })).toEqual({
      text: '',
      content: [],
      metadata: { knowledge: { verified: false, citations: [] } },
    });
  });
});
