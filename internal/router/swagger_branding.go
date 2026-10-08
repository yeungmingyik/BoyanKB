package router

import (
	"net/http"

	"github.com/Tencent/WeKnora/docs"
	"github.com/Tencent/WeKnora/internal/branding"
	"github.com/gin-gonic/gin"
	swaggerFiles "github.com/swaggo/files"
	ginSwagger "github.com/swaggo/gin-swagger"
)

func newSwaggerHandler() gin.HandlerFunc {
	static := ginSwagger.WrapHandler(swaggerFiles.Handler,
		ginSwagger.DefaultModelsExpandDepth(-1),
		ginSwagger.DocExpansion("list"),
		ginSwagger.DeepLinking(true),
		ginSwagger.PersistAuthorization(true),
	)
	return func(c *gin.Context) {
		if c.Param("any") != "/doc.json" {
			static(c)
			return
		}
		document, err := branding.Current().SwaggerJSON(docs.SwaggerInfo.ReadDoc())
		if err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to generate API documentation"})
			return
		}
		c.Data(http.StatusOK, "application/json; charset=utf-8", document)
	}
}
