package router

import (
	"github.com/Tencent/WeKnora/internal/handler"
	"github.com/gin-gonic/gin"
)

func RegisterBusinessWorkflowRoutes(r *gin.RouterGroup, h *handler.BusinessWorkflowHandler, g *rbacGuards) {
	r.GET("/business-workflow/readiness", g.Admin(), h.Readiness)
	r.GET("/business-workflow/skills", g.Viewer(), h.Skills)
	r.GET("/business-workflow/skills/:name", g.Viewer(), h.SkillDetail)
	r.GET("/business-workflow/team", g.Viewer(), h.Team)
	r.GET("/business-workflow/team/invitations", g.Viewer(), h.TeamInvitations)
	r.POST("/business-workflow/team/invitations", g.Viewer(), h.CreateTeamInvitation)
	r.DELETE("/business-workflow/team/invitations/:invitation_id", g.Viewer(), h.RevokeTeamInvitation)
	r.GET("/sessions/:id/documents", g.Viewer(), h.List)
	r.GET("/sessions/:id/documents/:job_id", g.Viewer(), h.Get)
	r.GET("/sessions/:id/documents/:job_id/download", g.Viewer(), h.Download)
	r.POST("/sessions/:session_id/documents/:job_id/confirm", g.Viewer(), h.Confirm)
	r.POST("/sessions/:session_id/documents/:job_id/generate", g.Viewer(), h.Generate)
}
