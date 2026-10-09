package businessworkflow

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
)

type RevisionSource struct {
	Authority       string `json:"authority"`
	ChunkID         string `json:"chunk_id"`
	KnowledgeID     string `json:"knowledge_id"`
	KnowledgeBaseID string `json:"knowledge_base_id"`
	Title           string `json:"title"`
}

func (s *Service) Revision(ctx context.Context, sessionID, id string) (map[string]interface{}, error) {
	job, err := s.Get(ctx, sessionID, id)
	if err != nil {
		return nil, err
	}
	if len(job.Payload) == 0 || len(job.Payload) > 512*1024 {
		return nil, ErrInvalid
	}
	var payload Payload
	decoder := json.NewDecoder(bytes.NewReader(job.Payload))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&payload) != nil || decoder.Decode(&struct{}{}) != io.EOF {
		return nil, ErrInvalid
	}
	if job.OutlineHash != digest(job.Payload) || payload.Scene != job.Scene || payload.Format != job.Format || payload.OutlineMarkdown != job.OutlineMarkdown {
		return nil, ErrConflict
	}
	if len(payload.Sources) == 0 || len(payload.Sources) > 30 || len(payload.Sources) != len(payload.VerifiedSources) || s.Skills == nil {
		return nil, ErrInvalid
	}
	requested := map[string]bool{}
	for _, source := range payload.Sources {
		if source.ChunkID == "" || requested[source.ChunkID] {
			return nil, ErrInvalid
		}
		requested[source.ChunkID] = true
	}
	for _, source := range payload.VerifiedSources {
		if !requested[source.ChunkID] || source.ID != source.ChunkID {
			return nil, ErrInvalid
		}
		delete(requested, source.ChunkID)
	}
	if err := s.verifySources(ctx, payload); err != nil {
		return nil, err
	}
	sources := make([]RevisionSource, 0, len(payload.VerifiedSources))
	for _, old := range payload.VerifiedSources {
		current, err := s.source(ctx, old.ChunkID)
		if err != nil {
			return nil, err
		}
		if current.SHA256 != old.SHA256 || current.KnowledgeID != old.KnowledgeID || current.KnowledgeBaseID != old.KnowledgeBaseID || current.Authority != old.Authority {
			return nil, ErrConflict
		}
		sources = append(sources, RevisionSource{Authority: current.Authority, ChunkID: current.ChunkID, KnowledgeID: current.KnowledgeID, KnowledgeBaseID: current.KnowledgeBaseID, Title: current.Title})
	}
	result := Public(*job)
	result["revision"] = payload.Proposal
	result["verified_sources"] = sources
	return result, nil
}
