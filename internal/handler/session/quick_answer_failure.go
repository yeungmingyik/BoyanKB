package session

import (
	"context"
	"errors"
	"io"
	"net"
	"time"

	"github.com/Tencent/WeKnora/internal/event"
	"github.com/Tencent/WeKnora/internal/logger"
	"github.com/Tencent/WeKnora/internal/models/api"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"github.com/google/uuid"
)

// Bound the whole quick-answer pipeline, including rewrite and retrieval.
const quickAnswerTimeout = 2 * time.Minute

func isTerminalQuickAnswerError(evt interfaces.StreamEvent) bool {
	if evt.Type != types.ResponseTypeError || !evt.Done {
		return false
	}
	return evt.Data["stage"] == "knowledge_qa_execution" || evt.Data["stage"] == "message_persistence"
}

func quickAnswerFailure(ctx context.Context, err error) (code, message string) {
	var networkErr net.Error
	var httpErr *api.HTTPError
	switch {
	case errors.Is(ctx.Err(), context.DeadlineExceeded), errors.Is(err, context.DeadlineExceeded):
		return "model_timeout", "本次问答已超时，请稍后重试。已生成的内容已保留。"
	case errors.As(err, &networkErr) && networkErr.Timeout():
		return "model_timeout", "模型响应超时，请稍后重试。已生成的内容已保留。"
	case errors.As(err, &httpErr) && (httpErr.StatusCode >= 500 || httpErr.StatusCode == 429):
		return "model_unavailable", "模型服务暂时不可用或繁忙，请稍后重试。已生成的内容已保留。"
	case err == nil, errors.Is(err, io.ErrUnexpectedEOF), errors.Is(err, io.EOF):
		return "model_stream_interrupted", "回答连接意外中断，请重新提问。已生成的内容可能不完整。"
	default:
		return "model_request_failed", "本次问答未能完成，请重试；如持续失败，请联系管理员检查模型服务。"
	}
}

// Persist failure separately from successful completion: partial answers must
// not enter conversation memory or trigger another model call for follow-ups.
func (h *Handler) failQuickAnswerTurn(ctx context.Context, streamCtx *sseStreamContext, err error) {
	code, message := quickAnswerFailure(ctx, err)
	ctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 10*time.Second)
	defer cancel()
	msg := streamCtx.assistantMessage
	step := ensureQuickAnswerStep(msg)
	step.ErrorCode, step.ErrorMessage = code, message
	logger.Warnf(ctx, "Quick answer failed: session=%s message=%s code=%s", msg.SessionID, msg.ID, code)

	msg.IsCompleted, msg.UpdatedAt = true, time.Now()
	saveErr := h.messageService.UpdateMessage(ctx, msg)
	// Publish even if persistence failed, using a fresh bounded context so an
	// exhausted database deadline cannot suppress the terminal notification.
	notifyCtx, notifyCancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
	defer notifyCancel()
	_ = streamCtx.eventBus.Emit(notifyCtx, event.Event{
		ID: uuid.NewString(), Type: event.EventError, SessionID: msg.SessionID,
		Data: event.ErrorData{Stage: "knowledge_qa_execution", ErrorCode: code, Error: message, SessionID: msg.SessionID},
	})
	if saveErr != nil {
		logger.Errorf(ctx, "Failed to save quick-answer failure: %v", saveErr)
		return
	}
}
