package modelcontext

import "encoding/json"

func (r *Registry) encodeBoyanRevisionResult(output string) string {
	var status struct {
		Revision *struct {
			Sources []struct {
				ChunkID string `json:"chunk_id"`
			} `json:"sources"`
		} `json:"revision"`
		VerifiedSources []struct {
			ChunkID         string `json:"chunk_id"`
			KnowledgeID     string `json:"knowledge_id"`
			KnowledgeBaseID string `json:"knowledge_base_id"`
			Title           string `json:"title"`
		} `json:"verified_sources"`
	}
	if r.sources == nil || json.Unmarshal([]byte(output), &status) != nil || status.Revision == nil || len(status.Revision.Sources) == 0 || len(status.Revision.Sources) != len(status.VerifiedSources) {
		return output
	}
	requested := map[string]bool{}
	for _, source := range status.Revision.Sources {
		if source.ChunkID == "" || requested[source.ChunkID] {
			return output
		}
		requested[source.ChunkID] = true
	}
	for _, source := range status.VerifiedSources {
		if !requested[source.ChunkID] || source.KnowledgeID == "" || source.KnowledgeBaseID == "" {
			return output
		}
		delete(requested, source.ChunkID)
	}
	for _, source := range status.VerifiedSources {
		r.sources.RegisterDocument(source.KnowledgeID)
		r.sources.RegisterKnowledgeBase(source.KnowledgeBaseID)
		r.sources.RegisterChunk(ChunkReference{ChunkID: source.ChunkID, KnowledgeID: source.KnowledgeID, KnowledgeBaseID: source.KnowledgeBaseID, DocumentTitle: source.Title})
	}
	return r.sources.CompactKnownText(output)
}
