package modelcontext

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/Tencent/WeKnora/internal/types"
)

func TestBoyanDocumentDecodesNestedChunkHandle(t *testing.T) {
	registry := NewRegistry(true)
	handle := registry.RegisterChunk(ChunkReference{ChunkID: "authorized-chunk", KnowledgeID: "authorized-document", KnowledgeBaseID: "authorized-kb"})
	args, _ := json.Marshal(map[string]interface{}{"operation": "propose", "sources": []map[string]string{{"chunk_id": handle}}, "outline_markdown": "待确认大纲"})
	calls := []types.LLMToolCall{{Function: types.FunctionCall{Name: "boyan_document", Arguments: string(args)}}}
	registry.DecodeToolCalls(calls)
	var decoded struct {
		Sources []struct {
			ChunkID string `json:"chunk_id"`
		} `json:"sources"`
	}
	if json.Unmarshal([]byte(calls[0].Function.Arguments), &decoded) != nil || len(decoded.Sources) != 1 || decoded.Sources[0].ChunkID != "authorized-chunk" {
		t.Fatal("nested source handle was not resolved")
	}
}

func TestBoyanRevisionRegistersOnlyVerifiedSourceHandles(t *testing.T) {
	const chunkID = "11111111-1111-4111-8111-111111111111"
	const documentID = "22222222-2222-4222-8222-222222222222"
	const kbID = "33333333-3333-4333-8333-333333333333"
	const invented = "44444444-4444-4444-8444-444444444444"
	data, _ := json.Marshal(map[string]interface{}{
		"id": "workflow-job", "state": "blocked",
		"revision":         map[string]interface{}{"sources": []map[string]string{{"chunk_id": chunkID}}, "requirements": map[string]string{"chunk_id": invented}, "document": map[string]interface{}{"title": "原草稿"}},
		"verified_sources": []map[string]string{{"chunk_id": chunkID, "knowledge_id": documentID, "knowledge_base_id": kbID, "title": "服务端标题"}},
	})
	registry := NewRegistry(true)
	output := registry.ModelToolResultForTool("boyan_document", &types.ToolResult{Success: true, Output: string(data)})
	if strings.Contains(output, chunkID) || strings.Contains(output, documentID) || strings.Contains(output, kbID) || !strings.Contains(output, `"chunk_id":"c1"`) || !strings.Contains(output, invented) || registry.ChunkHandle(invented) != "" {
		t.Fatal("verified mapping boundary failed")
	}
	if !strings.Contains(registry.DecodeOutputText(`<ref id="c1"/>`), `doc="服务端标题"`) {
		t.Fatal("source citation lost verified title")
	}
	calls := []types.LLMToolCall{{Function: types.FunctionCall{Name: "boyan_document", Arguments: `{"operation":"propose","sources":[{"chunk_id":"c1"}]}`}}}
	registry.DecodeToolCalls(calls)
	if !strings.Contains(calls[0].Function.Arguments, chunkID) {
		t.Fatal("revision handle cannot be proposed again")
	}
	if again := registry.ModelToolResultForTool("boyan_document", &types.ToolResult{Success: true, Output: string(data)}); again != output {
		t.Fatal("repeat status changed handles")
	}
}

func TestBoyanRevisionDoesNotRegisterFailedOrUnverifiedOutput(t *testing.T) {
	for _, tc := range []struct {
		name, tool, output string
		success            bool
	}{
		{"failed", "boyan_document", `{"revision":{"sources":[{"chunk_id":"verified"}]},"verified_sources":[{"chunk_id":"verified","knowledge_id":"doc","knowledge_base_id":"kb"}]}`, false},
		{"other_tool", "call_mcp_tool", `{"revision":{"sources":[{"chunk_id":"verified"}]},"verified_sources":[{"chunk_id":"verified","knowledge_id":"doc","knowledge_base_id":"kb"}]}`, true},
		{"unverified", "boyan_document", `{"revision":{"sources":[{"chunk_id":"verified"}]},"verified_sources":[]}`, true},
		{"mismatched", "boyan_document", `{"revision":{"sources":[{"chunk_id":"verified"}]},"verified_sources":[{"chunk_id":"other","knowledge_id":"doc","knowledge_base_id":"kb"}]}`, true},
		{"bad_json", "boyan_document", `{`, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			registry := NewRegistry(true)
			registry.ModelToolResultForTool(tc.tool, &types.ToolResult{Success: tc.success, Output: tc.output})
			if registry.ChunkHandle("verified") != "" || registry.ChunkHandle("other") != "" {
				t.Fatal("unverified status registered source")
			}
		})
	}
}
