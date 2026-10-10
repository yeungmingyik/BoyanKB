package handler

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Tencent/WeKnora/internal/application/service"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestGraphBackfillSubmitRejectsUnknownScopeAndTrailingJSON(t *testing.T) {
	gin.SetMode(gin.TestMode)
	for _, body := range []string{
		`{"knowledge_ids":[],"tenant_id":2}`,
		`{"knowledge_ids":[],"requested_by":"other"}`,
		`{"knowledge_ids":[]} {"retry_failed":true}`,
		`{"knowledge_ids":` + strings.Repeat(" ", 32768) + `[]}`,
	} {
		response := httptest.NewRecorder()
		ctx, _ := gin.CreateTestContext(response)
		ctx.Request = httptest.NewRequest(http.MethodPost, "/", strings.NewReader(body))
		(&GraphBackfillHandler{}).Submit(ctx)
		require.Equal(t, http.StatusBadRequest, response.Code)
		require.Equal(t, "no-store", response.Header().Get("Cache-Control"))
	}
}

func TestGraphBackfillResponseHasSafeErrors(t *testing.T) {
	gin.SetMode(gin.TestMode)
	for _, entry := range []struct {
		err    error
		status int
	}{
		{service.ErrGraphBackfillDenied, http.StatusForbidden},
		{service.ErrGraphBackfillChanged, http.StatusConflict},
		{service.ErrGraphBackfillUnavailable, http.StatusServiceUnavailable},
	} {
		response := httptest.NewRecorder()
		ctx, _ := gin.CreateTestContext(response)
		graphBackfillResponse(ctx, nil, entry.err)
		require.Equal(t, entry.status, response.Code)
		require.NotContains(t, response.Body.String(), "requested_by")
	}
}
