package config

import (
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"slices"
	"strings"
	"testing"
	"unicode"

	"gopkg.in/yaml.v3"
)

func TestChinesePromptOverridesPreserveTemplateContracts(t *testing.T) {
	root := filepath.Join("..", "..")
	variables := regexp.MustCompile(`\{\{[^{}]+\}\}`)
	for filename, count := range map[string]int{"agent_system_prompt.yaml": 7, "system_prompt.yaml": 6} {
		t.Run(filename, func(t *testing.T) {
			read := func(path string) promptTemplateFile {
				t.Helper()
				data, err := os.ReadFile(path)
				if err != nil {
					t.Fatal(err)
				}
				var templates promptTemplateFile
				if err := yaml.Unmarshal(data, &templates); err != nil {
					t.Fatal(err)
				}
				return templates
			}
			upstream := read(filepath.Join(root, "config", "prompt_templates", filename))
			localized := read(filepath.Join(root, "branding", "prompt_templates", filename))
			if len(upstream.Templates) != count || len(localized.Templates) != count {
				t.Fatal("template set changed")
			}
			for i, original := range upstream.Templates {
				translated := localized.Templates[i]
				for _, text := range []string{translated.Name, translated.Description, translated.Content} {
					if !strings.ContainsFunc(text, func(r rune) bool { return unicode.Is(unicode.Han, r) }) {
						t.Fatalf("template %s is not localized", translated.ID)
					}
				}
				if !strings.Contains(translated.Content, "BoyanKB") || strings.Contains(translated.Content, "WeKnora") || strings.Contains(translated.Content, "Tencent") {
					t.Fatalf("template %s identity mismatch", translated.ID)
				}
				originalVariables := variables.FindAllString(original.Content, -1)
				translatedVariables := variables.FindAllString(translated.Content, -1)
				slices.Sort(originalVariables)
				slices.Sort(translatedVariables)
				if !reflect.DeepEqual(originalVariables, translatedVariables) {
					t.Fatalf("template %s variables changed", translated.ID)
				}
				zh := translated.I18n["zh-CN"]
				if zh.Name != translated.Name || zh.Description != translated.Description {
					t.Fatalf("template %s Chinese metadata mismatch", translated.ID)
				}
				translated.Name, translated.Description, translated.Content = original.Name, original.Description, original.Content
				translated.I18n["zh-CN"] = original.I18n["zh-CN"]
				if !reflect.DeepEqual(original, translated) {
					t.Fatalf("template %s machine fields or other locales changed", original.ID)
				}
			}
		})
	}
}

func TestChinesePromptOverridesLoadToolAndSourceRules(t *testing.T) {
	pt, err := loadPromptTemplates(filepath.Join("..", "..", "branding"))
	if err != nil || pt == nil {
		t.Fatalf("override loading failed: %v", err)
	}
	for id, tokens := range map[string][]string{
		"default_kb":            {"{{language}}", "不得编造", "来源", "推断", "没有检索结果"},
		"progressive_rag_agent": {"<description>", "<generated_profile>", "search_knowledge", "mode=keyword", "mode=semantic", "hybrid", "read_document(id=dN)", "offset", "query", "list_documents"},
		"data_analyst":          {"data_schema", "data_analysis", "knowledge_id", "\"dataset\"", "SQL", "只读查询", "不得修改源数据"},
		"wiki_researcher":       {"wiki_search", "wiki_read_page", "read_document(id=dN)", "wiki_flag_issue", "[[slug|display name]]"},
		"wiki_fixer":            {"wiki_read_issue", "old_text", "content", "title", "summary", "page_type", "source_refs", "[[slug|display name]]", "只读要求"},
		"hybrid_rag_wiki_agent": {"wiki_search", "wiki_read_page", "search_knowledge", "read_document", "wiki_flag_issue", "[[slug|display name]]"},
		"skill_installer":       {"{{language}}", "<role>", "<inputs>", "<workflow>", "<prove_it_runs>", "<on_demand_dependencies>", "<isolation>", "<tool_guidelines>", "<constraints>", "<system_status>", "skill://", "shell_exec", "write_skill_file", "edit_skill_file", ".weknora/requirements.json", "/workspace", "<skill-dir>/.venv"},
	} {
		template := FindTemplateByID(pt, id)
		if template == nil {
			t.Fatalf("template %s missing", id)
		}
		for _, token := range tokens {
			if !strings.Contains(template.Content, token) {
				t.Fatalf("template %s lost required token %q", id, token)
			}
		}
	}
}
