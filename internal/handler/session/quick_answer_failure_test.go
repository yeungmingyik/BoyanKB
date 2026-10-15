package session

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/Tencent/WeKnora/internal/event"
	"github.com/Tencent/WeKnora/internal/models/api"
	"github.com/Tencent/WeKnora/internal/stream"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestQuickAnswerDeadlineDoesNotLimitAgentRuns(t *testing.T) {
	for _, mode := range []qaMode{qaModeNormal, qaModeAgent} {
		streams := stream.NewMemoryStreamManager()
		h := &Handler{streamManager: streams}
		req := &qaRequestContext{
			ctx: t.Context(), c: newSteerWiringGinContext(), sessionID: "s", requestID: "req",
			session: &types.Session{ID: "s"}, assistantMessage: &types.Message{ID: "m", SessionID: "s"},
		}
		started := time.Now()
		streamCtx := h.setupSSEStream(req, false, mode)
		deadline, ok := streamCtx.asyncCtx.Deadline()
		if mode == qaModeNormal {
			require.True(t, ok)
			require.WithinDuration(t, started.Add(2*time.Minute), deadline, time.Second)
		} else {
			require.False(t, ok, "longer agent workflows keep their existing timeouts")
		}
		streamCtx.cancel()
		require.NoError(t, streams.AppendEvent(context.Background(), "s", "m", interfaces.StreamEvent{Type: types.ResponseTypeComplete, Done: true}))
	}
}

func TestQuickAnswerFailurePersistsAndStopsStream(t *testing.T) {
	for _, tc := range []struct {
		name    string
		err     error
		code    string
		expired bool
	}{
		{"provider unavailable", &api.HTTPError{StatusCode: 503, Body: "private provider response"}, "model_unavailable", false},
		{"rate limited", &api.HTTPError{StatusCode: 429}, "model_unavailable", false},
		{"deadline", context.DeadlineExceeded, "model_timeout", true},
		{"unexpected EOF", io.ErrUnexpectedEOF, "model_stream_interrupted", false},
		{"missing done", nil, "model_stream_interrupted", false},
		{"provider configuration", errors.New("private endpoint and credentials"), "model_request_failed", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			messages := &imageCompletionMessages{}
			stream := &imageCompletionStream{}
			h := &Handler{messageService: messages, streamManager: stream}
			bus := event.NewEventBus()
			msg := &types.Message{ID: "m", SessionID: "s", Role: "assistant", Content: "已生成的内容", AgentTenantID: 2}
			appendQuickAnswerReasoning(msg, "已生成的思考")
			ctx := types.WithExecutionTenant(context.Background(), 1)
			if tc.expired {
				var cancel context.CancelFunc
				ctx, cancel = context.WithDeadline(ctx, time.Now().Add(-time.Second))
				defer cancel()
			}
			handler := h.setupStreamHandler(ctx, "s", "m", "req", 1, time.Now(), msg, bus)
			stream.onComplete = func() { require.NotNil(t, messages.saved, "completion must follow persistence") }
			h.failQuickAnswerTurn(ctx, &sseStreamContext{eventBus: bus, streamHandler: handler, assistantMessage: msg}, tc.err)
			require.NotNil(t, messages.saved)
			require.True(t, messages.saved.IsCompleted)
			require.Equal(t, uint64(1), messages.tenant)
			raw, err := json.Marshal(messages.saved)
			require.NoError(t, err)
			var restored types.Message
			require.NoError(t, json.Unmarshal(raw, &restored))
			require.Equal(t, "已生成的内容", restored.Content)
			require.Equal(t, tc.code, restored.AgentSteps[0].ErrorCode)
			require.Equal(t, "已生成的思考", restored.AgentSteps[0].ReasoningContent)
			require.NotContains(t, string(raw), "private")
			require.Len(t, stream.events, 1)
			require.True(t, isTerminalQuickAnswerError(stream.events[0]))
			require.Equal(t, tc.code, stream.events[0].Data["error_code"])
		})
	}
}

func TestQuickAnswerFailureNotifiesEvenWhenPersistenceFails(t *testing.T) {
	messages := &imageCompletionMessages{err: errors.New("database unavailable")}
	stream := &imageCompletionStream{}
	h := &Handler{messageService: messages, streamManager: stream}
	bus := event.NewEventBus()
	msg := &types.Message{ID: "m", SessionID: "s", Role: "assistant"}
	ctx := types.WithExecutionTenant(context.Background(), 1)
	handler := h.setupStreamHandler(ctx, "s", "m", "req", 1, time.Now(), msg, bus)
	h.failQuickAnswerTurn(ctx, &sseStreamContext{eventBus: bus, streamHandler: handler, assistantMessage: msg}, io.ErrUnexpectedEOF)
	require.Nil(t, messages.saved)
	require.Len(t, stream.events, 1)
	require.True(t, isTerminalQuickAnswerError(stream.events[0]))
}

func TestTerminalQuickAnswerErrorClosesLiveAndReplaySSE(t *testing.T) {
	gin.SetMode(gin.TestMode)
	for _, replay := range []bool{false, true} {
		t.Run(map[bool]string{false: "live", true: "replay"}[replay], func(t *testing.T) {
			events := []interfaces.StreamEvent{{Type: types.ResponseTypeError, Done: true,
				Content: "模型响应超时，请稍后重试。", Data: map[string]interface{}{
					"stage": "knowledge_qa_execution", "error_code": "model_timeout",
				}}}
			h := &Handler{sessionService: &stubSessionService{}, messageService: &stubMessageServiceForStream{}, streamManager: &stubStreamManager{events: events}}
			router := gin.New()
			router.GET("/stream/:session_id", func(c *gin.Context) {
				if replay {
					h.ContinueStream(c)
				} else {
					h.handleAgentEventsForSSE(c.Request.Context(), c, "s", "m", "req", nil, true, nil)
				}
			})
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer cancel()
			req := httptest.NewRequest(http.MethodGet, "/stream/s?message_id=m", nil).WithContext(ctx)
			resp := httptest.NewRecorder()
			router.ServeHTTP(resp, req)
			require.NoError(t, ctx.Err(), "must terminate on error, not wait for client timeout or a missing title")
			require.Equal(t, http.StatusOK, resp.Code)
			require.Contains(t, resp.Body.String(), "model_timeout")
			require.Contains(t, resp.Body.String(), `"done":true`)
		})
	}
}

func TestNonterminalToolErrorsDoNotCloseStream(t *testing.T) {
	require.False(t, isTerminalQuickAnswerError(interfaces.StreamEvent{Type: types.ResponseTypeError, Done: true, Data: map[string]interface{}{"stage": "tool_execution"}}))
	require.False(t, isTerminalQuickAnswerError(interfaces.StreamEvent{Type: types.ResponseTypeError, Data: map[string]interface{}{"stage": "knowledge_qa_execution"}}))
}
