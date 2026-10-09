package router

import (
	"github.com/Tencent/WeKnora/internal/handler"
	"github.com/gin-gonic/gin"
)

func RegisterPartnerProvisioningRoutes(r *gin.RouterGroup, h *handler.PartnerProvisioningHandler) {
	if h == nil {
		return
	}
	g := r.Group("/system/admin/partner-provisioning", h.RequireJWT)
	g.POST("", h.Create)
	g.GET("/:key", h.Get)
	g.POST("/:key/lease", h.Lease)
	g.PATCH("/:key", h.Update)
	g.POST("/:key/knowledge-creator", h.AssignKnowledgeCreator)
	g.POST("/:key/skills/install", h.InstallRestrictedSkills)
}
