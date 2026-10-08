package config

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/Tencent/WeKnora/internal/branding"
	"github.com/Tencent/WeKnora/internal/types"
)

func TestApplyPromptBrandingToBundledTemplates(t *testing.T) {
	path := filepath.Join(t.TempDir(), "brand.json")
	if err := os.WriteFile(path, []byte(`{"productName":"ExampleKB","productSlug":"example-kb"}`), 0600); err != nil {
		t.Fatal(err)
	}
	brand, err := branding.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	templates, err := loadPromptTemplates(filepath.Join("..", "..", "config"))
	if err != nil {
		t.Fatal(err)
	}
	before, err := loadPromptTemplates(filepath.Join("..", "..", "config"))
	if err != nil {
		t.Fatal(err)
	}
	applyPromptBranding(templates, brand)
	groups := [][]PromptTemplate{templates.SystemPrompt, templates.AgentSystemPrompt, templates.IntentPrompts, templates.Fallback}
	originals := [][]PromptTemplate{before.SystemPrompt, before.AgentSystemPrompt, before.IntentPrompts, before.Fallback}
	for g, group := range groups {
		changed := 0
		for i, template := range group {
			original := originals[g][i]
			if strings.Contains(template.Content, "WeKnora") || strings.Contains(template.Content, "developed by Tencent") {
				t.Fatalf("identity remained in template %s", template.ID)
			}
			if template.Content != original.Content {
				changed++
				if !strings.Contains(template.Content, "ExampleKB") {
					t.Fatalf("brand missing in template %s", template.ID)
				}
			}
			template.Content = original.Content
			if !reflect.DeepEqual(template, original) {
				t.Fatalf("non-content fields changed in template %s", template.ID)
			}
		}
		if changed == 0 {
			t.Fatalf("prompt group %d did not receive branding", g)
		}
	}
	for _, template := range templates.AgentSystemPrompt {
		if template.ID == "skill_installer" && !strings.Contains(template.Content, ".weknora/requirements.json") {
			t.Fatal("skill protocol path changed")
		}
	}
	if !reflect.DeepEqual(templates.ContextTemplate, before.ContextTemplate) || !reflect.DeepEqual(templates.Rewrite, before.Rewrite) {
		t.Fatal("unrelated prompt templates changed")
	}
	applyPromptBranding(nil, brand)
	cfg := &Config{PromptTemplates: templates}
	agent := &types.CustomAgent{Config: types.CustomAgentConfig{
		AgentMode: "quick-answer", SystemPromptID: templates.SystemPrompt[0].ID,
		SystemPrompt: "You are WeKnora, our saved custom assistant.",
	}}
	system, _ := cfg.ResolveCustomAgentPrompts(agent)
	if system != agent.Config.SystemPrompt {
		t.Fatal("saved custom prompt changed")
	}
	agent.Config.SystemPrompt = ""
	system, _ = cfg.ResolveCustomAgentPrompts(agent)
	if !strings.Contains(system, "ExampleKB") {
		t.Fatal("template reference did not resolve the configured identity")
	}
}
