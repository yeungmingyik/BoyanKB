package config

import "github.com/Tencent/WeKnora/internal/branding"

func applyPromptBranding(templates *PromptTemplatesConfig, brand branding.Config) {
	if templates == nil {
		return
	}
	for _, group := range [][]PromptTemplate{
		templates.SystemPrompt,
		templates.AgentSystemPrompt,
		templates.IntentPrompts,
		templates.Fallback,
	} {
		for i := range group {
			group[i].Content = brand.PromptText(group[i].Content)
		}
	}
}
