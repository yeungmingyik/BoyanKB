package handler

import (
	"encoding/json"
	"io"
	"mime"
	"net/http"

	"github.com/Tencent/WeKnora/internal/businessworkflow"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/gin-gonic/gin"
)

type BusinessWorkflowHandler struct{ service *businessworkflow.Service }

func NewBusinessWorkflowHandler(service *businessworkflow.Service) *BusinessWorkflowHandler {
	return &BusinessWorkflowHandler{service: service}
}

func (h *BusinessWorkflowHandler) Readiness(c *gin.Context) {
	principal, ok := types.PrincipalFromContext(c.Request.Context())
	if !ok || principal.Type != types.PrincipalWebUser {
		workflowError(c, businessworkflow.ErrDenied)
		return
	}
	data, err := businessworkflow.Readiness(c.Request.Context())
	if err != nil {
		workflowError(c, err)
		return
	}
	c.Header("Cache-Control", "no-store")
	c.JSON(http.StatusOK, gin.H{"success": true, "data": data})
}

func workflowError(c *gin.Context, err error) {
	status := businessworkflow.HTTPStatus(err)
	code := "document_service_unavailable"
	if status != 503 {
		code = err.Error()
	}
	c.JSON(status, gin.H{"success": false, "error": code})
}

func (h *BusinessWorkflowHandler) List(c *gin.Context) {
	jobs, err := h.service.List(c.Request.Context(), c.Param("id"))
	if err != nil {
		workflowError(c, err)
		return
	}
	data := make([]map[string]interface{}, 0, len(jobs))
	for _, job := range jobs {
		data = append(data, businessworkflow.Public(job))
	}
	c.Header("Cache-Control", "no-store")
	c.JSON(http.StatusOK, gin.H{"success": true, "data": data})
}

func (h *BusinessWorkflowHandler) Get(c *gin.Context) {
	job, err := h.service.Get(c.Request.Context(), c.Param("id"), c.Param("job_id"))
	if err != nil {
		workflowError(c, err)
		return
	}
	c.Header("Cache-Control", "no-store")
	c.JSON(http.StatusOK, gin.H{"success": true, "data": businessworkflow.Public(*job)})
}

func (h *BusinessWorkflowHandler) Confirm(c *gin.Context) {
	h.transition(c, true)
}

func (h *BusinessWorkflowHandler) Generate(c *gin.Context) {
	h.transition(c, false)
}

func (h *BusinessWorkflowHandler) transition(c *gin.Context, confirm bool) {
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, 2048)
	var input struct {
		OutlineHash string `json:"outline_hash"`
	}
	d := json.NewDecoder(c.Request.Body)
	d.DisallowUnknownFields()
	if d.Decode(&input) != nil {
		workflowError(c, businessworkflow.ErrInvalid)
		return
	}
	var extra interface{}
	if d.Decode(&extra) != io.EOF {
		workflowError(c, businessworkflow.ErrInvalid)
		return
	}
	var job *businessworkflow.Job
	var err error
	if confirm {
		job, err = h.service.Confirm(c.Request.Context(), c.Param("session_id"), c.Param("job_id"), input.OutlineHash)
	} else {
		job, err = h.service.Generate(c.Request.Context(), c.Param("session_id"), c.Param("job_id"), input.OutlineHash)
	}
	if err != nil {
		workflowError(c, err)
		return
	}
	c.Header("Cache-Control", "no-store")
	c.JSON(http.StatusOK, gin.H{"success": true, "data": businessworkflow.Public(*job)})
}

func (h *BusinessWorkflowHandler) Download(c *gin.Context) {
	job, err := h.service.Download(c.Request.Context(), c.Param("id"), c.Param("job_id"))
	if err != nil {
		workflowError(c, err)
		return
	}
	c.Header("Cache-Control", "no-store")
	c.Header("X-Content-Type-Options", "nosniff")
	c.Header("Content-Disposition", mime.FormatMediaType("attachment", map[string]string{"filename": job.Filename}))
	c.Data(http.StatusOK, job.MimeType, job.Artifact)
}
