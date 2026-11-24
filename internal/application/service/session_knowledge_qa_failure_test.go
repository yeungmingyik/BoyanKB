package service

import (
	"context"
	"io"
	"strings"
	"testing"
	"time"

	"github.com/Tencent/WeKnora/internal/event"
	"github.com/Tencent/WeKnora/internal/models/api"
	"github.com/Tencent/WeKnora/internal/models/chat"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/stretchr/testify/require"
)

type failureFallbackModel struct {
	captureChatModel
	chunks      []types.StreamResponse
	err         error
	closeStream bool
	streamCtx   context.Context
}

func (m *failureFallbackModel) ChatStream(ctx context.Context, _ []chat.Message, _ *chat.ChatOptions) (<-chan types.StreamResponse, error) {
	m.streamCtx = ctx
	if m.err != nil {
		return nil, m.err
	}
	ch := make(chan types.StreamResponse, len(m.chunks))
	for _, chunk := range m.chunks {
		ch <- chunk
	}
	if m.closeStream {
		close(ch)
	}
	return ch, nil
}

func TestHandleModelFallbackWaitsForTerminalResult(t *testing.T) {
	for _, tc := range []struct {
		name        string
		model       failureFallbackModel
		wantError   string
		wantContent string
		cancel      bool
		timeout     bool
	}{
		{
			name: "normal done without EOF",
			model: failureFallbackModel{chunks: []types.StreamResponse{
				{ResponseType: types.ResponseTypeAnswer, Content: "answer", Done: true},
			}},
			wantContent: "answer",
		},
		{
			name:      "initial 503",
			model:     failureFallbackModel{err: &api.HTTPError{StatusCode: 503}},
			wantError: "status 503",
		},
		{
			name: "provider error without EOF",
			model: failureFallbackModel{chunks: []types.StreamResponse{
				{ResponseType: types.ResponseTypeError, Content: "service unavailable"},
			}},
			wantError: "service unavailable",
		},
		{
			name: "partial provider error",
			model: failureFallbackModel{chunks: []types.StreamResponse{
				{ResponseType: types.ResponseTypeAnswer, Content: "partial answer"},
				{ResponseType: types.ResponseTypeError, Content: "service unavailable"},
			}},
			wantError: "service unavailable", wantContent: "partial answer",
		},
		{
			name: "empty EOF", model: failureFallbackModel{closeStream: true}, wantError: io.ErrUnexpectedEOF.Error(),
		},
		{
			name: "partial EOF",
			model: failureFallbackModel{closeStream: true, chunks: []types.StreamResponse{
				{ResponseType: types.ResponseTypeAnswer, Content: "partial answer"},
			}},
			wantError: io.ErrUnexpectedEOF.Error(), wantContent: "partial answer",
		},
		{name: "deadline without EOF", timeout: true, wantError: context.DeadlineExceeded.Error()},
		{name: "user stop", cancel: true, wantError: context.Canceled.Error()},
	} {
		t.Run(tc.name, func(t *testing.T) {
			bus := event.NewEventBus()
			var answers []event.AgentFinalAnswerData
			bus.On(event.EventAgentFinalAnswer, func(_ context.Context, evt event.Event) error {
				answers = append(answers, evt.Data.(event.AgentFinalAnswerData))
				return nil
			})
			cm := &types.ChatManage{PipelineRequest: types.PipelineRequest{
				FallbackStrategy: types.FallbackStrategyModel, FallbackPrompt: "Answer {{query}}",
				FallbackResponse: "fixed fallback", Query: "question",
			}, PipelineContext: types.PipelineContext{EventBus: bus.AsEventBusInterface()}}
			timeout := 2 * time.Second
			if tc.timeout {
				timeout = 20 * time.Millisecond
			}
			ctx, cancel := context.WithTimeout(context.Background(), timeout)
			defer cancel()
			if tc.cancel {
				cancel()
			}
			svc := &sessionService{modelService: &stubModelService{chatModel: &tc.model}}
			err := svc.handleFallbackResponse(ctx, cm)
			if tc.wantError == "" {
				require.NoError(t, err)
				require.NotNil(t, cm.ChatResponse)
				require.Equal(t, tc.wantContent, cm.ChatResponse.Content)
				require.True(t, answers[len(answers)-1].Done)
			} else {
				require.ErrorContains(t, err, tc.wantError)
				require.Nil(t, cm.ChatResponse)
				for _, answer := range answers {
					require.False(t, answer.Done)
				}
			}
			var content strings.Builder
			for _, answer := range answers {
				content.WriteString(answer.Content)
				require.True(t, answer.IsFallback)
			}
			require.Equal(t, tc.wantContent, content.String())
			require.Error(t, tc.model.streamCtx.Err())
			if !tc.cancel && !tc.timeout {
				require.NoError(t, ctx.Err())
			}
		})
	}
}
