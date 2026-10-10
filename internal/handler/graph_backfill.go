package handler

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"

	"github.com/Tencent/WeKnora/internal/application/service"
	"github.com/gin-gonic/gin"
)

type GraphBackfillHandler struct{ service *service.GraphBackfillService }

func NewGraphBackfillHandler(s *service.GraphBackfillService) *GraphBackfillHandler {
	return &GraphBackfillHandler{service: s}
}

func graphBackfillResponse(c *gin.Context, plan *service.GraphBackfillPlan, err error) {
	c.Header("Cache-Control", "no-store")
	if err == nil {
		c.JSON(http.StatusOK, gin.H{"success": true, "data": plan})
		return
	}
	status, code := http.StatusServiceUnavailable, "graph_backfill_unavailable"
	switch {
	case errors.Is(err, service.ErrGraphBackfillDenied):
		status, code = http.StatusForbidden, "graph_backfill_denied"
	case errors.Is(err, service.ErrGraphBackfillInvalid):
		status, code = http.StatusBadRequest, "graph_backfill_invalid"
	case errors.Is(err, service.ErrGraphBackfillChanged):
		status, code = http.StatusConflict, "graph_backfill_source_changed"
	}
	c.JSON(status, gin.H{"success": false, "error": code})
}

func (h *GraphBackfillHandler) Plan(c *gin.Context) {
	plan, err := h.service.Plan(c.Request.Context(), c.Param("id"), service.GraphBackfillInput{KnowledgeIDs: strings.Split(c.Query("knowledge_ids"), ",")})
	graphBackfillResponse(c, plan, err)
}

func (h *GraphBackfillHandler) Submit(c *gin.Context) {
	var input service.GraphBackfillInput
	decoder := json.NewDecoder(http.MaxBytesReader(c.Writer, c.Request.Body, 32768))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&input); err != nil {
		graphBackfillResponse(c, nil, service.ErrGraphBackfillInvalid)
		return
	}
	if err := decoder.Decode(&struct{}{}); err != io.EOF {
		graphBackfillResponse(c, nil, service.ErrGraphBackfillInvalid)
		return
	}
	plan, err := h.service.Submit(c.Request.Context(), c.Param("id"), input)
	graphBackfillResponse(c, plan, err)
}
