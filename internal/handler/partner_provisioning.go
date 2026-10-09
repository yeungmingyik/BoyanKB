package handler

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"

	"github.com/Tencent/WeKnora/internal/application/service"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/gin-gonic/gin"
)

type PartnerProvisioningHandler struct {
	service *service.PartnerProvisioningService
}

func NewPartnerProvisioningHandler(s *service.PartnerProvisioningService) *PartnerProvisioningHandler {
	return &PartnerProvisioningHandler{service: s}
}

func (h *PartnerProvisioningHandler) RequireJWT(c *gin.Context) {
	_, apiKey := types.TenantAPIKeyScopeFromContext(c.Request.Context())
	if apiKey || !strings.HasPrefix(c.GetHeader("Authorization"), "Bearer ") {
		c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"success": false, "error": "partner_jwt_required"})
		return
	}
	c.Next()
}

func bindPartnerBody(c *gin.Context, target any) bool {
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, 65536)
	d := json.NewDecoder(c.Request.Body)
	d.DisallowUnknownFields()
	if d.Decode(target) != nil || d.Decode(&struct{}{}) != io.EOF {
		c.JSON(http.StatusBadRequest, gin.H{"success": false, "error": "invalid_partner_request"})
		return false
	}
	return true
}

func writePartnerResult(c *gin.Context, value any, status int, err error) {
	if err == nil {
		c.JSON(status, gin.H{"success": true, "data": value})
		return
	}
	code := "partner_operation_failed"
	switch {
	case errors.Is(err, service.ErrPartnerForbidden):
		status, code = http.StatusForbidden, "partner_forbidden"
	case errors.Is(err, service.ErrPartnerConflict):
		status, code = http.StatusConflict, "partner_conflict"
	case errors.Is(err, service.ErrPartnerInvalid):
		status, code = http.StatusBadRequest, "invalid_partner_request"
	case errors.Is(err, service.ErrPartnerNotFound):
		status, code = http.StatusNotFound, "partner_not_found"
	default:
		status = http.StatusInternalServerError
	}
	c.JSON(status, gin.H{"success": false, "error": code})
}

func (h *PartnerProvisioningHandler) Create(c *gin.Context) {
	var in service.PartnerProvisioningInput
	if !bindPartnerBody(c, &in) {
		return
	}
	job, created, err := h.service.Provision(c.Request.Context(), in)
	status := http.StatusOK
	if created {
		status = http.StatusCreated
	}
	writePartnerResult(c, job, status, err)
}

func (h *PartnerProvisioningHandler) Get(c *gin.Context) {
	job, err := h.service.Get(c.Request.Context(), c.Param("key"))
	writePartnerResult(c, job, http.StatusOK, err)
}

type partnerLeaseRequest struct {
	LeaseHolder string `json:"lease_holder"`
	Release     bool   `json:"release"`
}

func (h *PartnerProvisioningHandler) Lease(c *gin.Context) {
	var in partnerLeaseRequest
	if !bindPartnerBody(c, &in) {
		return
	}
	job, err := h.service.Lease(c.Request.Context(), c.Param("key"), in.LeaseHolder, in.Release)
	writePartnerResult(c, job, http.StatusOK, err)
}

func (h *PartnerProvisioningHandler) Update(c *gin.Context) {
	var in service.PartnerProvisioningUpdate
	if !bindPartnerBody(c, &in) {
		return
	}
	job, err := h.service.Update(c.Request.Context(), c.Param("key"), in)
	writePartnerResult(c, job, http.StatusOK, err)
}

type partnerActionRequest struct {
	LeaseHolder string `json:"lease_holder"`
	Revision    uint64 `json:"revision"`
	UserID      string `json:"user_id,omitempty"`
}

func (h *PartnerProvisioningHandler) AssignKnowledgeCreator(c *gin.Context) {
	var in partnerActionRequest
	if !bindPartnerBody(c, &in) {
		return
	}
	job, err := h.service.AssignKnowledgeCreator(c.Request.Context(), c.Param("key"), in.LeaseHolder, in.Revision, in.UserID)
	writePartnerResult(c, job, http.StatusOK, err)
}

func (h *PartnerProvisioningHandler) InstallRestrictedSkills(c *gin.Context) {
	var in partnerActionRequest
	if !bindPartnerBody(c, &in) {
		return
	}
	if in.UserID != "" {
		writePartnerResult(c, nil, 0, service.ErrPartnerInvalid)
		return
	}
	job, err := h.service.InstallRestrictedSkills(c.Request.Context(), c.Param("key"), in.LeaseHolder, in.Revision)
	writePartnerResult(c, job, http.StatusOK, err)
}
