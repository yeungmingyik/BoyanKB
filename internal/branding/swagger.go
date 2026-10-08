package branding

import (
	"encoding/json"
	"fmt"
)

func (c Config) SwaggerJSON(raw string) ([]byte, error) {
	if !c.configured {
		return []byte(raw), nil
	}
	var document map[string]any
	if err := json.Unmarshal([]byte(raw), &document); err != nil {
		return nil, err
	}
	if document == nil {
		return nil, fmt.Errorf("swagger document must be an object")
	}
	rewriteDescriptions(document, c)
	info, ok := document["info"].(map[string]any)
	if !ok {
		info = map[string]any{}
		document["info"] = info
	}
	info["title"] = c.ProductName + " API"
	info["description"] = c.Description
	delete(info, "termsOfService")
	delete(info, "contact")
	if c.Links.Repository != "" {
		info["contact"] = map[string]any{"name": c.ProductName, "url": c.Links.Repository}
	}
	return json.Marshal(document)
}

func rewriteDescriptions(value any, cfg Config) {
	switch node := value.(type) {
	case map[string]any:
		for key, child := range node {
			if key == "description" {
				if text, ok := child.(string); ok {
					node[key] = cfg.ProductText(text)
					continue
				}
			}
			rewriteDescriptions(child, cfg)
		}
	case []any:
		for _, child := range node {
			rewriteDescriptions(child, cfg)
		}
	}
}
