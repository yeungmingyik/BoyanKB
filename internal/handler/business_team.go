package handler

import (
	"encoding/json"
	"io"
	"net/http"
	"strconv"

	"github.com/Tencent/WeKnora/internal/businessworkflow"
	"github.com/gin-gonic/gin"
)

func teamRequest(c *gin.Context) bool {
	c.Header("Cache-Control", "no-store")
	if c.Request.URL.RawQuery != "" {
		workflowError(c, businessworkflow.ErrInvalid)
		return false
	}
	return true
}

func (h *BusinessWorkflowHandler) Team(c *gin.Context) {
	if !teamRequest(c) {
		return
	}
	data, err := h.service.Team(c.Request.Context())
	if err != nil {
		workflowError(c, err)
		return
	}
	c.JSON(http.StatusOK, gin.H{"success": true, "data": data})
}

func (h *BusinessWorkflowHandler) TeamInvitations(c *gin.Context) {
	if !teamRequest(c) {
		return
	}
	data, err := h.service.TeamInvitations(c.Request.Context())
	if err != nil {
		workflowError(c, err)
		return
	}
	c.JSON(http.StatusOK, gin.H{"success": true, "data": data})
}

func (h *BusinessWorkflowHandler) CreateTeamInvitation(c *gin.Context) {
	if !teamRequest(c) {
		return
	}
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, 1024)
	var input businessworkflow.TeamInvitationInput
	decoder := json.NewDecoder(c.Request.Body)
	decoder.DisallowUnknownFields()
	if decoder.Decode(&input) != nil || decoder.Decode(&struct{}{}) != io.EOF {
		workflowError(c, businessworkflow.ErrInvalid)
		return
	}
	data, err := h.service.CreateTeamInvitation(c.Request.Context(), input)
	if err != nil {
		workflowError(c, err)
		return
	}
	c.JSON(http.StatusCreated, gin.H{"success": true, "data": data})
}

func (h *BusinessWorkflowHandler) RevokeTeamInvitation(c *gin.Context) {
	if !teamRequest(c) {
		return
	}
	id, err := strconv.ParseUint(c.Param("invitation_id"), 10, 64)
	if err != nil || id == 0 {
		workflowError(c, businessworkflow.ErrInvalid)
		return
	}
	if err := h.service.RevokeTeamInvitation(c.Request.Context(), id); err != nil {
		workflowError(c, err)
		return
	}
	c.JSON(http.StatusOK, gin.H{"success": true})
}
