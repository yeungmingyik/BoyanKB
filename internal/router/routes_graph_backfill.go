package router

import (
	"github.com/Tencent/WeKnora/internal/handler"
	"github.com/gin-gonic/gin"
)

func RegisterGraphBackfillRoutes(r *gin.RouterGroup, h *handler.GraphBackfillHandler, g *rbacGuards) {
	r.GET("/knowledge-bases/:id/graph-backfill", g.Admin(), h.Plan)
	r.POST("/knowledge-bases/:id/graph-backfill", g.Admin(), h.Submit)
}
