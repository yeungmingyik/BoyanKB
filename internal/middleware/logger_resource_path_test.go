package middleware

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/gin-gonic/gin"
	"github.com/sirupsen/logrus"
)

func TestLoggerResourceCapabilityPathRedaction(t *testing.T) {
	for _, status := range []int{http.StatusOK, http.StatusNotFound, http.StatusBadGateway} {
		var output bytes.Buffer
		log := logrus.New()
		log.SetOutput(&output)
		log.SetFormatter(&logrus.JSONFormatter{})
		router := gin.New()
		router.ContextWithFallback = true
		router.Use(Logger())
		router.GET("/r/:token", func(c *gin.Context) {
			if c.Param("token") != "fixture-resource-secret" {
				t.Fatal("logging changed capability routing")
			}
			c.Status(status)
		})
		request := httptest.NewRequest(http.MethodGet, "/r/fixture-resource-secret?token=fixture-query-secret", nil)
		request.Header.Set("Referer", "https://example.invalid/r/fixture-referrer-secret")
		request.Header.Set("User-Agent", "fixture-user-agent-secret")
		request = request.WithContext(context.WithValue(request.Context(), types.LoggerContextKey, logrus.NewEntry(log)))
		response := httptest.NewRecorder()
		router.ServeHTTP(response, request)
		if response.Code != status {
			t.Fatal("logging changed the response status")
		}
		for _, secret := range []string{"fixture-resource-secret", "fixture-query-secret", "fixture-referrer-secret", "fixture-user-agent-secret"} {
			if strings.Contains(output.String(), secret) {
				t.Fatal("request log contains a synthetic credential")
			}
		}
		var entry map[string]interface{}
		if err := json.Unmarshal(output.Bytes(), &entry); err != nil {
			t.Fatal(err)
		}
		if entry["path"] != "/r/[redacted]?token=%2A%2A%2A" || entry["status_code"] != float64(status) {
			t.Fatal("sanitized diagnostic fields missing")
		}
	}
}
