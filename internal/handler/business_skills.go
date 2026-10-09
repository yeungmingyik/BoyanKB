package handler

import (
	"net/http"

	"github.com/Tencent/WeKnora/internal/businessworkflow"
	"github.com/gin-gonic/gin"
)

func (h *BusinessWorkflowHandler) Skills(c *gin.Context) {
	c.Header("Cache-Control", "no-store")
	if c.Request.URL.RawQuery != "" {
		workflowError(c, businessworkflow.ErrInvalid)
		return
	}
	data, err := h.service.SkillCatalog(c.Request.Context())
	if err != nil {
		workflowError(c, err)
		return
	}
	c.JSON(http.StatusOK, gin.H{"success": true, "data": data})
}

func (h *BusinessWorkflowHandler) SkillDetail(c *gin.Context) {
	c.Header("Cache-Control", "no-store")
	if c.Request.URL.RawQuery != "" {
		workflowError(c, businessworkflow.ErrInvalid)
		return
	}
	data, err := h.service.SkillDetail(c.Request.Context(), c.Param("name"))
	if err != nil {
		workflowError(c, err)
		return
	}
	c.JSON(http.StatusOK, gin.H{"success": true, "data": data})
}
