package handler

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/Tencent/WeKnora/internal/businessworkflow"
	"github.com/gin-gonic/gin"
)

func TestBusinessSkillsRejectSelectorQueriesAndAnonymousCalls(t *testing.T) {
	h := NewBusinessWorkflowHandler(&businessworkflow.Service{})
	r := gin.New()
	r.GET("/business-workflow/skills", h.Skills)
	r.GET("/business-workflow/skills/:name", h.SkillDetail)
	for _, test := range []struct {
		path string
		want int
	}{
		{"/business-workflow/skills?tenant_id=2", http.StatusBadRequest},
		{"/business-workflow/skills/boyan-courseware?path=/etc/passwd", http.StatusBadRequest},
		{"/business-workflow/skills", http.StatusForbidden},
		{"/business-workflow/skills/boyan-courseware", http.StatusForbidden},
	} {
		t.Run(test.path, func(t *testing.T) {
			response := httptest.NewRecorder()
			r.ServeHTTP(response, httptest.NewRequest(http.MethodGet, test.path, nil))
			if response.Code != test.want || response.Header().Get("Cache-Control") != "no-store" {
				t.Fatalf("status=%d, cache=%q", response.Code, response.Header().Get("Cache-Control"))
			}
		})
	}
}
