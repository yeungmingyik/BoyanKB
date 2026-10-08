package branding

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/Tencent/WeKnora/docs"
)

func TestSwaggerJSONUpdatesPresentationAndPreservesSchema(t *testing.T) {
	cfg := Config{ProductName: "ExampleKB", Description: "Knowledge workspace", configured: true}
	raw := docs.SwaggerInfo.ReadDoc()
	data, err := cfg.SwaggerJSON(raw)
	if err != nil {
		t.Fatal(err)
	}
	var original, branded map[string]any
	if err := json.Unmarshal([]byte(raw), &original); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, &branded); err != nil {
		t.Fatal(err)
	}
	info := branded["info"].(map[string]any)
	if info["title"] != "ExampleKB API" || info["description"] != "Knowledge workspace" {
		t.Fatalf("unexpected info: %v", info)
	}
	if _, exists := info["contact"]; exists {
		t.Fatal("empty repository link must omit upstream contact")
	}
	if _, exists := info["termsOfService"]; exists {
		t.Fatal("upstream terms link must be omitted")
	}
	delete(original, "info")
	delete(branded, "info")
	rewriteDescriptions(original, cfg)
	if !reflect.DeepEqual(original, branded) {
		t.Fatal("Swagger structure changed outside presentation fields")
	}
	if !strings.Contains(string(data), "github_com_Tencent_WeKnora_") {
		t.Fatal("schema identifiers must retain their upstream names")
	}
	if docs.SwaggerInfo.ReadDoc() != raw {
		t.Fatal("runtime branding mutated the generated Swagger source")
	}
}

func TestSwaggerJSONContactAndDescriptionBoundaries(t *testing.T) {
	cfg := Config{ProductName: "ExampleKB", Description: "Knowledge workspace", Links: Links{Repository: "https://example.org/source"}, configured: true}
	raw := `{"info":{"title":"WeKnora API","contact":{"url":"https://github.com/Tencent/WeKnora"}},"definitions":{"WeKnora":{"description":"WeKnora API using WeKnora Cloud and X-WeKnora-Desktop-Token","example":"WeKnora","$ref":"#/definitions/WeKnora"}},"license":{"name":"MIT","copyright":"Tencent WeKnora"}}`
	data, err := cfg.SwaggerJSON(raw)
	if err != nil {
		t.Fatal(err)
	}
	var result map[string]any
	if err := json.Unmarshal(data, &result); err != nil {
		t.Fatal(err)
	}
	contact := result["info"].(map[string]any)["contact"].(map[string]any)
	if contact["name"] != "ExampleKB" || contact["url"] != "https://example.org/source" {
		t.Fatalf("unexpected contact: %v", contact)
	}
	definition := result["definitions"].(map[string]any)["WeKnora"].(map[string]any)
	if definition["description"] != "ExampleKB API using WeKnora Cloud and X-WeKnora-Desktop-Token" || definition["example"] != "WeKnora" || definition["$ref"] != "#/definitions/WeKnora" {
		t.Fatalf("unexpected definition: %v", definition)
	}
	if result["license"].(map[string]any)["copyright"] != "Tencent WeKnora" {
		t.Fatal("copyright changed")
	}
	for _, raw := range []string{"null", "[]", "{"} {
		if _, err := cfg.SwaggerJSON(raw); err == nil {
			t.Fatalf("expected error for %q", raw)
		}
	}
}
