package handler

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestPartnerProvisioningHTTPRejectsAPIKeysAndMissingJWT(t *testing.T) {
	gin.SetMode(gin.TestMode)
	for _, mode := range []string{"missing", "tenant-key", "platform-key"} {
		t.Run(mode, func(t *testing.T) {
			r := gin.New()
			h := NewPartnerProvisioningHandler(nil)
			r.Use(func(c *gin.Context) {
				if mode != "missing" {
					scope := types.TenantAPIKeyScope{FullAccess: true}
					if mode == "platform-key" {
						scope.ScopeType = types.APIKeyScopePlatform
					}
					c.Request = c.Request.WithContext(context.WithValue(c.Request.Context(), types.TenantAPIKeyScopeContextKey, scope))
				}
			})
			r.POST("/provision", h.RequireJWT, func(c *gin.Context) { t.Fatal("API key reached provisioning") })
			req := httptest.NewRequest(http.MethodPost, "/provision", nil)
			if mode != "missing" {
				req.Header.Set("Authorization", "Bearer syntactic-header")
			}
			w := httptest.NewRecorder()
			r.ServeHTTP(w, req)
			require.Equal(t, http.StatusForbidden, w.Code)
		})
	}
}

func TestPartnerProvisioningHTTPRejectsUnknownAndOversizedBodies(t *testing.T) {
	gin.SetMode(gin.TestMode)
	for _, body := range []string{`{"lease_holder":"id","owner_id":"attacker"}`, `{} {}`, `{"lease_holder":"` + strings.Repeat("x", 65536) + `"}`} {
		r := gin.New()
		r.POST("/lease", func(c *gin.Context) {
			var value partnerLeaseRequest
			require.False(t, bindPartnerBody(c, &value))
		})
		w := httptest.NewRecorder()
		r.ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/lease", strings.NewReader(body)))
		require.Equal(t, http.StatusBadRequest, w.Code)
	}
}

func TestPartnerProvisioningHTTPHidesInternalErrorDetails(t *testing.T) {
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	writePartnerResult(c, nil, 0, errors.New("SQL secret must never appear"))
	require.Equal(t, http.StatusInternalServerError, w.Code)
	require.NotContains(t, w.Body.String(), "secret")
	require.NotContains(t, w.Body.String(), "SQL")
}
