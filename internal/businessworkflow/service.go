package businessworkflow

import (
	"context"
	"encoding/json"
	"errors"
	"maps"
	"slices"
	"time"

	"github.com/Tencent/WeKnora/internal/boyanskills"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"github.com/google/uuid"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

type Service struct {
	DB     *gorm.DB
	Models interfaces.ModelService
	Shares interfaces.KBShareService
	Render func(context.Context, string, uint64, Payload, string) (Artifact, error)
	Review func(context.Context, Payload, Artifact) (Review, error)
	Skills func(context.Context, string, string) (map[string]string, error)
}

func NewService(db *gorm.DB, models interfaces.ModelService, shares interfaces.KBShareService) *Service {
	s := &Service{DB: db, Models: models, Shares: shares}
	s.Render = renderHTTP
	s.Review = s.review
	s.Skills = func(ctx context.Context, scene, format string) (map[string]string, error) {
		hashes := map[string]string{}
		for _, name := range []string{"boyan-" + scene, "boyan-" + format} {
			if _, err := boyanskills.Read(ctx, db, types.CallerFromContext(ctx).TenantID, name); err != nil {
				return nil, ErrDenied
			}
			guide, err := boyanskills.Fixed(name)
			if err != nil {
				return nil, ErrDenied
			}
			hashes[name] = guide.SHA256
		}
		return hashes, nil
	}
	return s
}

func (s *Service) session(ctx context.Context, sessionID string) error {
	c := types.CallerFromContext(ctx)
	p, ok := types.PrincipalFromContext(ctx)
	if c.TenantID == 0 || c.UserID == "" || !ok || p.Type != types.PrincipalWebUser {
		return ErrDenied
	}
	var count int64
	if s.DB.WithContext(ctx).Model(&types.Session{}).Where("id = ? AND tenant_id = ? AND user_id = ?", sessionID, c.TenantID, c.UserID).Count(&count).Error != nil || count != 1 {
		return ErrDenied
	}
	return nil
}

func (s *Service) source(ctx context.Context, id string) (Source, error) {
	provision, err := s.Provision(ctx)
	if err != nil {
		return Source{}, err
	}
	var chunk types.Chunk
	if id == "" || s.DB.WithContext(ctx).Where("id = ? AND is_enabled = ?", id, true).First(&chunk).Error != nil {
		return Source{}, ErrDenied
	}
	var knowledge types.Knowledge
	if s.DB.WithContext(ctx).Where("id = ?", chunk.KnowledgeID).First(&knowledge).Error != nil || knowledge.KnowledgeBaseID != chunk.KnowledgeBaseID || knowledge.TenantID != chunk.TenantID {
		return Source{}, ErrDenied
	}
	if knowledge.KnowledgeBaseID != provision.State.KnowledgeBaseID && !slices.Contains([]string(provision.HQKnowledgeBaseIDs), knowledge.KnowledgeBaseID) {
		return Source{}, ErrDenied
	}
	authority := "partner"
	if slices.Contains([]string(provision.HQKnowledgeBaseIDs), knowledge.KnowledgeBaseID) {
		if knowledge.TenantID != provision.HQTenantID {
			return Source{}, ErrDenied
		}
		authority = "headquarters"
	} else if knowledge.TenantID != provision.TenantID {
		return Source{}, ErrDenied
	}
	var planner types.CustomAgent
	if s.DB.WithContext(ctx).Where("id = ? AND tenant_id = ? AND created_by = ?", provision.State.AgentIDs["planner"], provision.TenantID, provision.OwnerUserID).First(&planner).Error != nil || planner.Config.KBSelectionMode != "selected" || !slices.Contains(planner.Config.KnowledgeBases, knowledge.KnowledgeBaseID) {
		return Source{}, ErrDenied
	}
	var kb types.KnowledgeBase
	if s.DB.WithContext(ctx).Where("id = ? AND tenant_id = ?", knowledge.KnowledgeBaseID, knowledge.TenantID).First(&kb).Error != nil {
		return Source{}, ErrDenied
	}
	c := types.CallerFromContext(ctx)
	if c.TenantID != knowledge.TenantID {
		if s.Shares == nil {
			return Source{}, ErrDenied
		}
		permission, allowed, err := s.Shares.CheckTenantKBPermission(ctx, knowledge.KnowledgeBaseID, c.TenantID, c.Role)
		if err != nil || !allowed || !permission.HasPermission(types.OrgRoleViewer) {
			return Source{}, ErrDenied
		}
	}
	if types.AuthorizeTenantAPIKeyKnowledgeBases(ctx, knowledge.KnowledgeBaseID) != nil {
		return Source{}, ErrDenied
	}
	if err := validateRendererText("source.title", knowledge.Title, 300, true); err != nil {
		return Source{}, err
	}
	if err := validateRendererText("source.excerpt", chunk.Content, 24000, true); err != nil {
		return Source{}, err
	}
	return Source{Authority: authority, ID: chunk.ID, ChunkID: chunk.ID, KnowledgeID: knowledge.ID, KnowledgeBaseID: knowledge.KnowledgeBaseID, Title: knowledge.Title, Excerpt: chunk.Content, SHA256: digest([]byte(chunk.Content))}, nil
}

func (s *Service) verifySources(ctx context.Context, p Payload) error {
	hashes, err := s.Skills(ctx, p.Scene, p.Format)
	if err != nil {
		return err
	}
	if !maps.Equal(hashes, p.SkillHashes) {
		return ErrConflict
	}
	if len(p.VerifiedSources) == 0 {
		return ErrDenied
	}
	for _, old := range p.VerifiedSources {
		current, err := s.source(ctx, old.ChunkID)
		if err != nil {
			return err
		}
		if current.SHA256 != old.SHA256 || current.KnowledgeID != old.KnowledgeID || current.KnowledgeBaseID != old.KnowledgeBaseID || current.Authority != old.Authority {
			return ErrConflict
		}
	}
	return nil
}

func (s *Service) Propose(ctx context.Context, sessionID string, p Proposal) (*Job, error) {
	if err := s.session(ctx, sessionID); err != nil {
		return nil, err
	}
	if err := ValidateProposal(p); err != nil {
		return nil, err
	}
	c := types.CallerFromContext(ctx)
	payload := Payload{Proposal: p, TemplateVersion: "boyan-v1"}
	hashes, err := s.Skills(ctx, p.Scene, p.Format)
	if err != nil {
		return nil, err
	}
	payload.SkillHashes = hashes
	seen := map[string]bool{}
	for _, src := range p.Sources {
		if seen[src.ChunkID] {
			return nil, ErrInvalid
		}
		seen[src.ChunkID] = true
		verified, err := s.source(ctx, src.ChunkID)
		if err != nil {
			return nil, err
		}
		payload.VerifiedSources = append(payload.VerifiedSources, verified)
	}
	data, err := json.Marshal(payload)
	if err != nil || len(data) > 512*1024 {
		return nil, ErrInvalid
	}
	job := &Job{ID: uuid.NewString(), TenantID: c.TenantID, UserID: c.UserID, SessionID: sessionID, State: AwaitingConfirmation, Scene: p.Scene, OutlineHash: digest(data), OutlineMarkdown: p.OutlineMarkdown, Format: p.Format, Payload: data}
	err = s.DB.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		var session types.Session
		if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).Where("id = ? AND tenant_id = ? AND user_id = ?", sessionID, c.TenantID, c.UserID).First(&session).Error; err != nil {
			return ErrDenied
		}
		var active int64
		if err := tx.Model(&Job{}).Where("session_id = ? AND state IN ?", sessionID, []string{Generating, Reviewing}).Count(&active).Error; err != nil {
			return err
		}
		if active > 0 {
			return ErrConflict
		}
		if err := tx.Model(&Job{}).Where("session_id = ? AND tenant_id = ? AND user_id = ? AND scene = ? AND format = ? AND state IN ?", sessionID, c.TenantID, c.UserID, p.Scene, p.Format, []string{AwaitingConfirmation, Approved}).Updates(map[string]interface{}{"state": Superseded, "confirmed_at": nil}).Error; err != nil {
			return err
		}
		return tx.Create(job).Error
	})
	return job, err
}

func (s *Service) Get(ctx context.Context, sessionID, id string) (*Job, error) {
	if err := s.session(ctx, sessionID); err != nil {
		return nil, err
	}
	c := types.CallerFromContext(ctx)
	var job Job
	if s.DB.WithContext(ctx).Where("id = ? AND tenant_id = ? AND user_id = ? AND session_id = ?", id, c.TenantID, c.UserID, sessionID).First(&job).Error != nil {
		return nil, ErrDenied
	}
	if (job.State == Generating || job.State == Reviewing) && time.Since(job.UpdatedAt) > 5*time.Minute {
		s.DB.WithContext(ctx).Model(&Job{}).Where("id = ? AND state = ? AND updated_at = ?", job.ID, job.State, job.UpdatedAt).Updates(map[string]interface{}{"state": Failed, "failure_code": "generation_interrupted", "artifact": nil})
		job.State = Failed
		job.Artifact = nil
		job.FailureCode = "generation_interrupted"
	}
	return &job, nil
}

func (s *Service) List(ctx context.Context, sessionID string) ([]Job, error) {
	if err := s.session(ctx, sessionID); err != nil {
		return nil, err
	}
	c := types.CallerFromContext(ctx)
	if err := s.DB.WithContext(ctx).Model(&Job{}).Where("tenant_id = ? AND user_id = ? AND session_id = ? AND state IN ? AND updated_at < ?", c.TenantID, c.UserID, sessionID, []string{Generating, Reviewing}, time.Now().UTC().Add(-5*time.Minute)).Updates(map[string]interface{}{"state": Failed, "failure_code": "generation_interrupted", "artifact": nil}).Error; err != nil {
		return nil, err
	}
	jobs := []Job{}
	err := s.DB.WithContext(ctx).Omit("artifact", "payload").Where("tenant_id = ? AND user_id = ? AND session_id = ?", c.TenantID, c.UserID, sessionID).Order("created_at DESC").Limit(20).Find(&jobs).Error
	return jobs, err
}

func (s *Service) Confirm(ctx context.Context, sessionID, id, hash string) (*Job, error) {
	job, err := s.Get(ctx, sessionID, id)
	if err != nil {
		return nil, err
	}
	if job.State != AwaitingConfirmation || hash == "" || hash != job.OutlineHash || hash != digest(job.Payload) {
		return nil, ErrConflict
	}
	var payload Payload
	if json.Unmarshal(job.Payload, &payload) != nil {
		return nil, ErrInvalid
	}
	if err := s.verifySources(ctx, payload); err != nil {
		return nil, err
	}
	now := time.Now().UTC()
	result := s.DB.WithContext(ctx).Model(&Job{}).Where("id = ? AND state = ? AND outline_hash = ?", job.ID, AwaitingConfirmation, hash).Updates(map[string]interface{}{"state": Approved, "confirmed_at": now})
	if result.Error != nil {
		return nil, result.Error
	}
	if result.RowsAffected != 1 {
		return nil, ErrConflict
	}
	job.State, job.ConfirmedAt = Approved, &now
	return job, nil
}

func (s *Service) Generate(ctx context.Context, sessionID, id, hash string) (*Job, error) {
	job, err := s.Get(ctx, sessionID, id)
	if err != nil {
		return nil, err
	}
	if job.State == Published && job.OutlineHash == hash {
		return job, nil
	}
	if job.State != Approved || job.ConfirmedAt == nil || hash != job.OutlineHash || hash != digest(job.Payload) {
		return nil, ErrConflict
	}
	var payload Payload
	if json.Unmarshal(job.Payload, &payload) != nil {
		return nil, ErrInvalid
	}
	if err := s.verifySources(ctx, payload); err != nil {
		return nil, err
	}
	err = s.DB.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		var session types.Session
		if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).Where("id = ? AND tenant_id = ? AND user_id = ?", sessionID, job.TenantID, job.UserID).First(&session).Error; err != nil {
			return ErrDenied
		}
		claim := tx.Model(&Job{}).Where("id = ? AND state = ? AND outline_hash = ? AND confirmed_at IS NOT NULL", id, Approved, hash).Update("state", Generating)
		if claim.Error != nil {
			return claim.Error
		}
		if claim.RowsAffected != 1 {
			return ErrConflict
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	bounded, cancel := context.WithTimeout(ctx, 180*time.Second)
	defer cancel()
	fail := func(code string) (*Job, error) {
		cleanup, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
		defer cancel()
		s.DB.WithContext(cleanup).Model(&Job{}).Where("id = ? AND state IN ?", id, []string{Generating, Reviewing}).Updates(map[string]interface{}{"state": Failed, "failure_code": code, "artifact": nil})
		return nil, ErrUnavailable
	}
	artifact, err := s.Render(bounded, job.ID, job.TenantID, payload, hash)
	if err != nil {
		return fail("render_failed")
	}
	if err := artifact.Validate(job.Format); err != nil {
		return fail("artifact_invalid")
	}
	result := s.DB.WithContext(bounded).Model(&Job{}).Where("id = ? AND state = ?", id, Generating).Updates(map[string]interface{}{"state": Reviewing, "artifact": artifact.Bytes, "filename": artifact.Filename, "mime_type": artifact.MimeType, "artifact_sha256": artifact.SHA256})
	if result.Error != nil || result.RowsAffected != 1 {
		return fail("candidate_store_failed")
	}
	review, err := s.Review(bounded, payload, artifact)
	if err != nil {
		return fail("review_unverified")
	}
	reviewBytes, _ := json.Marshal(review)
	verified, err := ParseReview(string(reviewBytes))
	if err != nil {
		return fail("review_invalid")
	}
	if err := s.verifySources(bounded, payload); err != nil {
		return fail("source_changed")
	}
	state := Published
	values := map[string]interface{}{"review_json": reviewBytes}
	if !verified.Passed {
		state = Blocked
		values["artifact"] = nil
	}
	values["state"] = state
	result = s.DB.WithContext(bounded).Model(&Job{}).Where("id = ? AND state = ? AND outline_hash = ?", id, Reviewing, hash).Updates(values)
	if result.Error != nil || result.RowsAffected != 1 {
		return fail("publish_failed")
	}
	return s.Get(ctx, sessionID, id)
}

func (s *Service) Download(ctx context.Context, sessionID, id string) (*Job, error) {
	job, err := s.Get(ctx, sessionID, id)
	if err != nil {
		return nil, err
	}
	if job.State != Published || job.ConfirmedAt == nil || job.OutlineHash != digest(job.Payload) {
		return nil, ErrConflict
	}
	review, err := ParseReview(string(job.ReviewJSON))
	if err != nil || !review.Passed || len(job.Artifact) == 0 || digest(job.Artifact) != job.ArtifactSHA256 {
		return nil, ErrConflict
	}
	var payload Payload
	if json.Unmarshal(job.Payload, &payload) != nil {
		return nil, ErrInvalid
	}
	if err := s.verifySources(ctx, payload); err != nil {
		return nil, err
	}
	return job, nil
}

func Public(job Job) map[string]interface{} {
	data, _ := json.Marshal(job)
	result := map[string]interface{}{}
	_ = json.Unmarshal(data, &result)
	if len(job.ReviewJSON) > 0 {
		var review Review
		if json.Unmarshal(job.ReviewJSON, &review) == nil {
			result["review"] = review
		}
	}
	return result
}

func HTTPStatus(err error) int {
	switch {
	case errors.Is(err, ErrDenied):
		return 403
	case errors.Is(err, ErrConflict):
		return 409
	case errors.Is(err, ErrInvalid):
		return 400
	default:
		return 503
	}
}
