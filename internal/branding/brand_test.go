package branding

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeBrandConfig(t *testing.T, data string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "brand.json")
	if err := os.WriteFile(path, []byte(data), 0600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestLoadAcceptsSharedBrandConfig(t *testing.T) {
	path := writeBrandConfig(t, `{"productName":"ExampleKB","productSlug":"example-kb","description":"Knowledge workspace","links":{"repository":""},"colors":{"primary":"#123456"},"features":{"upstreamCli":false}}`)
	cfg, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if !cfg.configured || cfg.ProductName != "ExampleKB" || cfg.ProductSlug != "example-kb" || cfg.Links.Repository != "" {
		t.Fatalf("unexpected config: %+v", cfg)
	}
}

func TestLoadRejectsUnsafeBrandIdentifiers(t *testing.T) {
	for name, data := range map[string]string{
		"missing name":     `{"productSlug":"example"}`,
		"blank name":       `{"productName":" ","productSlug":"example"}`,
		"control name":     `{"productName":"Example\nKB","productSlug":"example"}`,
		"empty slug":       `{"productName":"ExampleKB","productSlug":""}`,
		"header injection": `{"productName":"ExampleKB","productSlug":"example\r\nX: yes"}`,
		"path traversal":   `{"productName":"ExampleKB","productSlug":"../example"}`,
		"null":             `null`,
		"malformed":        `{`,
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := Load(writeBrandConfig(t, data)); err == nil {
				t.Fatal("expected invalid branding to fail")
			}
		})
	}
}

func TestLoadFromEnvMissingDefaultAndExplicitFailure(t *testing.T) {
	previous := current.Load()
	t.Cleanup(func() { current.Store(previous) })
	t.Chdir(t.TempDir())
	t.Setenv("BRANDING_CONFIG", "")
	if err := LoadFromEnv(); err != nil {
		t.Fatal(err)
	}
	if Current() != Default() {
		t.Fatal("missing default should preserve upstream behavior")
	}
	t.Setenv("BRANDING_CONFIG", filepath.Join(t.TempDir(), "missing.json"))
	if err := LoadFromEnv(); err == nil {
		t.Fatal("explicit missing config must fail")
	}
	t.Setenv("BRANDING_CONFIG", writeBrandConfig(t, `{"productName":"ExampleKB","productSlug":"example-kb"}`))
	if err := LoadFromEnv(); err != nil {
		t.Fatal(err)
	}
	if Name() != "ExampleKB" || Slug() != "example-kb" {
		t.Fatal("loaded config was not activated")
	}
}

func TestProductTextPreservesProvidersAndProtocolIdentifiers(t *testing.T) {
	cfg := Config{ProductName: "ExampleKB", configured: true}
	source := "WeKnora API; WeKnora Cloud; WeKnora 云服务; WeKnora云服务; WeKnoraCloud; Hunyuan; Tencent; https://github.com/Tencent/WeKnora; X-WeKnora-Desktop-Token; github_com_Tencent_WeKnora_types.Model"
	want := strings.Replace(source, "WeKnora API", "ExampleKB API", 1)
	if got := cfg.ProductText(source); got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
	if got := Default().ProductText(source); got != source {
		t.Fatal("unconfigured branding must not modify text")
	}
}

func TestPromptTextOnlyChangesProductIdentity(t *testing.T) {
	cfg := Config{ProductName: "ExampleKB", configured: true}
	source := "You are WeKnora, an assistant developed by Tencent. Help the user.\nUse .weknora/requirements.json and WEKNORA_API_KEY.\nTencent is a provider. WeKnora Cloud is a provider.\nYou are the WeKnora Skill Installer.\nUser quote: You are WeKnora."
	want := "You are ExampleKB, an assistant. Help the user.\nUse .weknora/requirements.json and WEKNORA_API_KEY.\nTencent is a provider. WeKnora Cloud is a provider.\nYou are the ExampleKB Skill Installer.\nUser quote: You are WeKnora."
	if got := cfg.PromptText(source); got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
	if got := Default().PromptText(source); got != source {
		t.Fatal("unconfigured branding must not modify prompts")
	}
}
