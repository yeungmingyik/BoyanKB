package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"slices"
	"strings"
	"testing"
	"unicode"

	"github.com/Tencent/WeKnora/internal/types"
	"gopkg.in/yaml.v3"
)

func TestChinesePromptOverridesPreserveTemplateContracts(t *testing.T) {
	root := filepath.Join("..", "..")
	variables := regexp.MustCompile(`\{\{[^{}]+\}\}`)
	for filename, count := range map[string]int{
		"agent_system_prompt.yaml": 7, "system_prompt.yaml": 6,
		"context_template.yaml": 4, "rewrite.yaml": 3,
		"fallback.yaml": 5, "intent_prompts.yaml": 7,
	} {
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
				if strings.Contains(translated.Content, "WeKnora") || strings.Contains(translated.Content, "Tencent") {
					t.Fatalf("template %s identity mismatch", translated.ID)
				}
				if strings.Contains(original.Content, "WeKnora") && !strings.Contains(translated.Content, "BoyanKB") {
					t.Fatalf("template %s lost product identity", translated.ID)
				}
				for field, pair := range map[string][2]string{
					"content": {original.Content, translated.Content},
					"user":    {original.User, translated.User},
				} {
					if pair[0] != "" && !strings.ContainsFunc(pair[1], func(r rune) bool { return unicode.Is(unicode.Han, r) }) {
						t.Fatalf("template %s %s is not localized", translated.ID, field)
					}
					originalVariables := variables.FindAllString(pair[0], -1)
					translatedVariables := variables.FindAllString(pair[1], -1)
					if filename == "fallback.yaml" && original.Mode != "model" {
						originalVariables = slices.DeleteFunc(originalVariables, func(value string) bool { return value == "{{language}}" })
					}
					slices.Sort(originalVariables)
					slices.Sort(translatedVariables)
					if !slices.Equal(originalVariables, translatedVariables) {
						t.Fatalf("template %s %s variables changed", translated.ID, field)
					}
				}
				zh := translated.I18n["zh-CN"]
				if zh.Name != translated.Name || zh.Description != translated.Description {
					t.Fatalf("template %s Chinese metadata mismatch", translated.ID)
				}
				translated.Name, translated.Description, translated.Content = original.Name, original.Description, original.Content
				translated.User = original.User
				translated.I18n["zh-CN"] = original.I18n["zh-CN"]
				if !reflect.DeepEqual(original, translated) {
					t.Fatalf("template %s machine fields or other locales changed", original.ID)
				}
			}
		})
	}
}

func TestChineseContextOverridesRenderSourceAndRequestSeparately(t *testing.T) {
	pt, err := loadPromptTemplates(filepath.Join("..", "..", "branding"))
	if err != nil || pt == nil {
		t.Fatalf("override loading failed: %v", err)
	}
	for _, template := range pt.ContextTemplate {
		t.Run(template.ID, func(t *testing.T) {
			rendered := types.RenderPromptPlaceholders(template.Content, types.PlaceholderValues{
				"contexts": "SOURCE_SENTINEL", "query": "USER_REQUEST_SENTINEL",
				"current_time": "2026-10-10", "current_week": "星期六", "language": "简体中文",
			})
			if strings.Contains(rendered, "{{") || strings.Count(rendered, "SOURCE_SENTINEL") != 1 || strings.Count(rendered, "USER_REQUEST_SENTINEL") != 1 {
				t.Fatal("context variables did not render exactly once")
			}
			sourceLabel := strings.Index(rendered, "来源数据")
			source := strings.Index(rendered, "SOURCE_SENTINEL")
			requestLabel := strings.Index(rendered, "用户请求")
			request := strings.Index(rendered, "USER_REQUEST_SENTINEL")
			if sourceLabel < 0 || source <= sourceLabel || requestLabel <= source || request <= requestLabel {
				t.Fatal("source data and user request are not separated")
			}
		})
	}
}

func TestChineseRewriteOverridesPreserveMachineOutputs(t *testing.T) {
	upstream, err := loadPromptTemplates(filepath.Join("..", "..", "config"))
	if err != nil || upstream == nil {
		t.Fatalf("upstream loading failed: %v", err)
	}
	localized, err := loadPromptTemplates(filepath.Join("..", "..", "branding"))
	if err != nil || localized == nil {
		t.Fatalf("override loading failed: %v", err)
	}
	original, translated := DefaultTemplate(upstream.Rewrite), DefaultTemplate(localized.Rewrite)
	if original == nil || translated == nil || original.ID != "default_rewrite" || translated.ID != original.ID {
		t.Fatal("default rewrite identity changed")
	}
	intents := regexp.MustCompile("(?m)^([1-9])\\. `([^`]+)`")
	if !reflect.DeepEqual(intents.FindAllStringSubmatch(original.Content, -1), intents.FindAllStringSubmatch(translated.Content, -1)) {
		t.Fatal("intent priority or machine identifiers changed")
	}
	for _, token := range []string{"<no_image_attached />", "<no_document_attached />", "<images_uploaded>", "OCR"} {
		if !strings.Contains(translated.Content, token) {
			t.Fatalf("attachment protocol missing %s", token)
		}
	}
	for _, rule := range []string{"问题仍是问题，指令仍是指令", "来源限制", "输出格式", "语言要求", "默认选择 `kb_search`", "只输出一个 JSON 对象", "非空描述"} {
		if !strings.Contains(translated.Content, rule) {
			t.Fatalf("rewrite constraint missing %s", rule)
		}
	}
	objects := regexp.MustCompile(`\{"rewrite_query":[^\n]+\}`)
	originalJSON := objects.FindAllString(original.Content, -1)
	translatedJSON := objects.FindAllString(translated.Content, -1)
	if len(originalJSON) < 10 || len(translatedJSON) != len(originalJSON) {
		t.Fatal("rewrite examples or schema missing")
	}
	for i, text := range translatedJSON {
		var originalObject, translatedObject map[string]string
		if json.Unmarshal([]byte(originalJSON[i]), &originalObject) != nil || json.Unmarshal([]byte(text), &translatedObject) != nil {
			t.Fatalf("rewrite example %d is not a string-valued JSON object", i)
		}
		if len(translatedObject) != 3 || translatedObject["rewrite_query"] == "" || translatedObject["intent"] != originalObject["intent"] {
			t.Fatalf("rewrite example %d fields or intent changed", i)
		}
		originalImage, originalHasImage := originalObject["image_description"]
		translatedImage, translatedHasImage := translatedObject["image_description"]
		if !originalHasImage || !translatedHasImage || (originalImage == "") != (translatedImage == "") {
			t.Fatalf("rewrite example %d attachment contract changed", i)
		}
	}
}

func TestChineseFixedFallbacksAreDirectUserReplies(t *testing.T) {
	pt, err := loadPromptTemplates(filepath.Join("..", "..", "branding"))
	if err != nil || pt == nil {
		t.Fatalf("override loading failed: %v", err)
	}
	fixed := 0
	for _, template := range pt.Fallback {
		if template.Mode == "model" {
			for _, token := range []string{"{{kb_documents}}", "{{language}}", "{{query}}"} {
				if !strings.Contains(template.Content, token) {
					t.Fatalf("model fallback %s missing %s", template.ID, token)
				}
			}
			continue
		}
		fixed++
		if !strings.Contains(template.Content, "抱歉") {
			t.Fatalf("fixed fallback %s is not a Chinese user reply", template.ID)
		}
		for _, token := range []string{"{{", "}}", "Language Rule", "ALWAYS", "语言要求", "默认回答语言", "你是"} {
			if strings.Contains(template.Content, token) {
				t.Fatalf("fixed fallback %s leaks template instructions", template.ID)
			}
		}
	}
	if fixed != 3 {
		t.Fatal("fixed fallback set changed")
	}
	model := FindTemplateByID(pt, "default_fallback_prompt")
	if model == nil || !strings.Contains(model.Content, "不能用它概括尚未阅读的正文") || !strings.Contains(model.Content, "不得猜测") {
		t.Fatal("model fallback lost source evidence constraints")
	}
}

func TestChinesePromptOverridesArePackaged(t *testing.T) {
	data, err := os.ReadFile(filepath.Join("..", "..", "docker", "Dockerfile.app-boyankb"))
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"system_prompt", "agent_system_prompt", "context_template", "rewrite", "fallback", "intent_prompts"} {
		line := "COPY --chown=appuser:appuser branding/prompt_templates/" + name + ".yaml /app/config/prompt_templates/" + name + ".yaml"
		if strings.Count(string(data), line) != 1 {
			t.Fatalf("localized %s is not packaged exactly once", name)
		}
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
