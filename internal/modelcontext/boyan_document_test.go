package modelcontext

import (
	"encoding/json"
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
