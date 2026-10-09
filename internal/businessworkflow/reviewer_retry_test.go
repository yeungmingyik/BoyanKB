package businessworkflow

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/Tencent/WeKnora/internal/models/chat"
	"github.com/Tencent/WeKnora/internal/types"
)

type clarificationResponse struct {
	response *types.ChatResponse
	err      error
}

type clarificationChatBase = chat.Chat

type clarificationChat struct {
	clarificationChatBase
	t         *testing.T
	steps     []clarificationResponse
	requests  [][]chat.Message
	deadlines []time.Time
}

func (m *clarificationChat) Chat(ctx context.Context, messages []chat.Message, options *chat.ChatOptions) (*types.ChatResponse, error) {
	m.t.Helper()
	deadline, ok := ctx.Deadline()
	if !ok || time.Until(deadline) > 60*time.Second || options.Temperature != 0 || options.MaxCompletionTokens != 4096 {
		m.t.Fatal("unbounded review request")
	}
	m.requests = append(m.requests, append([]chat.Message(nil), messages...))
	m.deadlines = append(m.deadlines, deadline)
	index := len(m.requests) - 1
	if index >= len(m.steps) {
		m.t.Fatal("unexpected review retry")
	}
	return m.steps[index].response, m.steps[index].err
}

func reviewResponse(content string) clarificationResponse {
	return clarificationResponse{response: &types.ChatResponse{Content: content, FinishReason: "stop"}}
}

const reviewContradiction = `{"passed":false,"issues":[{"location":"页眉","problem":"可优化排版","severity":"suggestion","suggestion":"对齐标题"}]}`
const reviewPass = `{"passed":true,"issues":[]}`
const reviewBlocking = `{"passed":false,"issues":[{"location":"预算合计","problem":"合计金额与明细不一致","severity":"blocking","suggestion":"按明细重新计算合计"}]}`

func TestReviewClarificationAcceptsOnlyModelResolvedOutcome(t *testing.T) {
	for _, tc := range []struct {
		name, corrected string
		passed          bool
	}{
		{"resolved_pass", reviewPass, true},
		{"resolved_blocking", reviewBlocking, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			model := &clarificationChat{t: t, steps: []clarificationResponse{reviewResponse(reviewContradiction), reviewResponse(tc.corrected)}}
			review, err := reviewWithClarification(context.Background(), model, "review-system", "same-document-and-sources")
			if err != nil || review.Passed != tc.passed || len(model.requests) != 2 {
				t.Fatalf("unexpected outcome: %v", err)
			}
			messages := model.requests[1]
			if len(messages) != 4 || !reflect.DeepEqual(messages[:2], model.requests[0]) || messages[2].Role != "assistant" || messages[2].Content != reviewContradiction || messages[3].Role != "user" || messages[3].Content != reviewClarificationInstructions {
				t.Fatal("clarification changed evidence or lost previous answer")
			}
		})
	}
}

func TestReviewClarificationFailsClosedOnRepeatedContradictionOrInvalidRetry(t *testing.T) {
	for _, tc := range []struct {
		name string
		step clarificationResponse
	}{
		{"contradiction", reviewResponse(reviewContradiction)},
		{"false_empty", reviewResponse(`{"passed":false,"issues":[]}`)},
		{"timeout", clarificationResponse{err: context.DeadlineExceeded}},
		{"malformed", reviewResponse(`{"passed":true}`)},
		{"trailing", reviewResponse(reviewPass + ` {}`)},
		{"extra_field", reviewResponse(`{"passed":true,"issues":[],"override":true}`)},
		{"truncated", clarificationResponse{response: &types.ChatResponse{Content: reviewPass, FinishReason: "length"}}},
		{"tool_call", clarificationResponse{response: &types.ChatResponse{Content: reviewPass, FinishReason: "stop", ToolCalls: []types.LLMToolCall{{}}}}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			model := &clarificationChat{t: t, steps: []clarificationResponse{reviewResponse(reviewContradiction), tc.step}}
			review, err := reviewWithClarification(context.Background(), model, "system", "document")
			if !errors.Is(err, ErrUnavailable) || review.Passed || len(model.requests) != 2 {
				t.Fatal("unverified clarification accepted")
			}
		})
	}
}

func TestReviewClarificationDoesNotRetryDefinitiveOrInvalidInitialReview(t *testing.T) {
	for _, tc := range []struct {
		name string
		step clarificationResponse
		fail bool
	}{
		{"pass", reviewResponse(reviewPass), false},
		{"blocking", reviewResponse(reviewBlocking), false},
		{"true_with_blocking", reviewResponse(strings.Replace(reviewBlocking, `"passed":false`, `"passed":true`, 1)), false},
		{"timeout", clarificationResponse{err: context.DeadlineExceeded}, true},
		{"malformed", reviewResponse(`not JSON`), true},
		{"nil_response", clarificationResponse{}, true},
		{"tool_call", clarificationResponse{response: &types.ChatResponse{Content: reviewPass, FinishReason: "stop", ToolCalls: []types.LLMToolCall{{}}}}, true},
		{"truncated", clarificationResponse{response: &types.ChatResponse{Content: reviewPass, FinishReason: "length"}}, true},
		{"filtered", clarificationResponse{response: &types.ChatResponse{Content: reviewPass, FinishReason: "content_filter"}}, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			model := &clarificationChat{t: t, steps: []clarificationResponse{tc.step}}
			_, err := reviewWithClarification(context.Background(), model, "system", "document")
			if (err != nil) != tc.fail || len(model.requests) != 1 {
				t.Fatal("unexpected initial retry or outcome")
			}
		})
	}
}

func TestReviewClarificationHonorsParentDeadline(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	parentDeadline, _ := ctx.Deadline()
	model := &clarificationChat{t: t, steps: []clarificationResponse{reviewResponse(reviewContradiction), reviewResponse(reviewPass)}}
	if _, err := reviewWithClarification(ctx, model, "system", "document"); err != nil {
		t.Fatal(err)
	}
	for _, deadline := range model.deadlines {
		if !deadline.Equal(parentDeadline) {
			t.Fatal("review exceeded parent deadline")
		}
	}
	cancel()
	if _, err := reviewWithClarification(ctx, model, "system", "document"); !errors.Is(err, ErrUnavailable) || len(model.requests) != 2 {
		t.Fatal("canceled review called model")
	}
}
