package service

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/Tencent/WeKnora/internal/application/repository"
	"github.com/Tencent/WeKnora/internal/boyanskills"
	"github.com/Tencent/WeKnora/internal/config"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"github.com/google/uuid"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

var (
	ErrPartnerForbidden = errors.New("partner provisioning forbidden")
	ErrPartnerConflict  = errors.New("partner provisioning conflict")
	ErrPartnerInvalid   = errors.New("invalid partner provisioning request")
	ErrPartnerNotFound  = errors.New("partner provisioning not found")
	partnerKeyPattern   = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{2,63}$`)
	partnerHashPattern  = regexp.MustCompile(`^[a-f0-9]{64}$`)
)

type PartnerProvisioningInput struct {
	PartnerKey         string   `json:"partner_key"`
	Name               string   `json:"name"`
	Description        string   `json:"description"`
	HQTenantID         uint64   `json:"hq_tenant_id"`
	HQKnowledgeBaseIDs []string `json:"hq_knowledge_base_ids"`
	SpecHash           string   `json:"spec_hash"`
}

type PartnerProvisioningUpdate struct {
	LeaseHolder string                         `json:"lease_holder"`
	Revision    uint64                         `json:"revision"`
	Status      string                         `json:"status"`
	State       types.PartnerProvisioningState `json:"state"`
}

type PartnerProvisioningService struct {
	db       *gorm.DB
	settings interfaces.SystemSettingService
	now      func() time.Time
}

func NewPartnerProvisioningService(db *gorm.DB, _ *config.Config, settings interfaces.SystemSettingService) *PartnerProvisioningService {
	return &PartnerProvisioningService{db: db, settings: settings, now: time.Now}
}

func validatePartnerInput(in *PartnerProvisioningInput) error {
	in.Name = strings.TrimSpace(in.Name)
	in.Description = strings.TrimSpace(in.Description)
	for _, r := range in.Name {
		if r < 32 || r == 127 {
			return ErrPartnerInvalid
		}
	}
	if !partnerKeyPattern.MatchString(in.PartnerKey) || !partnerHashPattern.MatchString(in.SpecHash) || in.HQTenantID == 0 || in.Name == "" || len([]rune(in.Name)) > 120 || len([]rune(in.Description)) > 1000 || len(in.HQKnowledgeBaseIDs) == 0 || len(in.HQKnowledgeBaseIDs) > 32 {
		return ErrPartnerInvalid
	}
	seen := map[string]bool{}
	for _, id := range in.HQKnowledgeBaseIDs {
		if _, err := uuid.Parse(id); err != nil || seen[id] {
			return ErrPartnerInvalid
		}
		seen[id] = true
	}
	sort.Strings(in.HQKnowledgeBaseIDs)
	return nil
}

func partnerRequestHash(in PartnerProvisioningInput) string {
	b, _ := json.Marshal(in)
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:])
}

func partnerActor(ctx context.Context) (string, uint64, error) {
	if _, ok := types.TenantAPIKeyScopeFromContext(ctx); ok {
		return "", 0, ErrPartnerForbidden
	}
	uid, ok := types.UserIDFromContext(ctx)
	tenant, _ := ctx.Value(types.TenantIDContextKey).(uint64)
	if !ok || uid == "" || types.IsSyntheticUserID(uid) || tenant == 0 {
		return "", 0, ErrPartnerForbidden
	}
	return uid, tenant, nil
}

func authorizePartner(tx *gorm.DB, uid string, hq uint64) error {
	var user types.User
	if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).Where("id = ? AND is_active = ? AND is_system_admin = ?", uid, true, true).First(&user).Error; err != nil {
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return ErrPartnerForbidden
		}
		return err
	}
	return requirePartnerOwner(tx, uid, hq)
}

func requirePartnerOwner(tx *gorm.DB, uid string, tenant uint64) error {
	var count int64
	if err := tx.Model(&types.TenantMember{}).Where("user_id = ? AND tenant_id = ? AND role = ? AND status = ?", uid, tenant, types.TenantRoleOwner, types.TenantMemberStatusActive).Count(&count).Error; err != nil {
		return err
	}
	if count != 1 {
		return ErrPartnerForbidden
	}
	var t types.Tenant
	if err := tx.Where("id = ? AND status = ?", tenant, "active").First(&t).Error; err != nil {
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return ErrPartnerForbidden
		}
		return err
	}
	return nil
}

func (s *PartnerProvisioningService) Provision(ctx context.Context, in PartnerProvisioningInput) (*types.PartnerProvisioningJob, bool, error) {
	if err := validatePartnerInput(&in); err != nil {
		return nil, false, err
	}
	uid, hq, err := partnerActor(ctx)
	if err != nil || hq != in.HQTenantID {
		return nil, false, ErrPartnerForbidden
	}
	hash := partnerRequestHash(in)
	var job types.PartnerProvisioningJob
	created := false
	err = s.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		if err := authorizePartner(tx, uid, hq); err != nil {
			return err
		}
		findErr := tx.Where("partner_key = ?", in.PartnerKey).First(&job).Error
		if findErr == nil {
			if job.RequestHash != hash || job.HQTenantID != hq {
				return ErrPartnerConflict
			}
			return requirePartnerOwner(tx, uid, job.TenantID)
		}
		if !errors.Is(findErr, gorm.ErrRecordNotFound) {
			return findErr
		}
		var count int64
		if err := tx.Model(&types.KnowledgeBase{}).Where("tenant_id = ? AND id IN ?", hq, in.HQKnowledgeBaseIDs).Count(&count).Error; err != nil {
			return err
		}
		if count != int64(len(in.HQKnowledgeBaseIDs)) {
			return ErrPartnerInvalid
		}
		gb := int64(10)
		if s.settings != nil {
			gb = s.settings.GetInt(ctx, "tenant.default_storage_quota_gb", "WEKNORA_TENANT_DEFAULT_STORAGE_QUOTA_GB", 10)
		}
		if gb <= 0 || gb > 1048576 {
			return ErrPartnerInvalid
		}
		tenant := &types.Tenant{Name: in.Name, Description: in.Description, StorageQuota: gb * 1024 * 1024 * 1024}
		svc := NewTenantService(repository.NewTenantRepository(tx), repository.NewStorageBackendRepository(tx))
		if _, err := svc.CreateTenant(ctx, tenant); err != nil {
			return err
		}
		now := s.now().UTC()
		member := &types.TenantMember{UserID: uid, TenantID: tenant.ID, Role: types.TenantRoleOwner, Status: types.TenantMemberStatusActive, JoinedAt: now}
		if err := tx.Create(member).Error; err != nil {
			return err
		}
		policy := &types.TenantSandboxConfigEntity{ID: uuid.NewString(), TenantID: tenant.ID, Name: types.SandboxWorkspacePolicyConfigName, SandboxType: "disabled", Config: &types.TenantSandboxConfig{SandboxType: "disabled"}}
		if err := tx.Create(policy).Error; err != nil {
			return err
		}
		job = types.PartnerProvisioningJob{PartnerKey: in.PartnerKey, RequestHash: hash, SpecHash: in.SpecHash, HQTenantID: hq, HQKnowledgeBaseIDs: in.HQKnowledgeBaseIDs, TenantID: tenant.ID, OwnerUserID: uid, Status: "provisioning", State: types.PartnerProvisioningState{}, Revision: 1}
		if err := tx.Create(&job).Error; err != nil {
			return err
		}
		created = true
		return nil
	})
	if err != nil {
		return nil, false, err
	}
	return &job, created, nil
}

func (s *PartnerProvisioningService) withJob(ctx context.Context, key string, fn func(*gorm.DB, *types.PartnerProvisioningJob) error) (*types.PartnerProvisioningJob, error) {
	if !partnerKeyPattern.MatchString(key) {
		return nil, ErrPartnerInvalid
	}
	uid, hq, err := partnerActor(ctx)
	if err != nil {
		return nil, err
	}
	var job types.PartnerProvisioningJob
	err = s.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		if err := authorizePartner(tx, uid, hq); err != nil {
			return err
		}
		if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).Where("partner_key = ? AND hq_tenant_id = ?", key, hq).First(&job).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				return ErrPartnerNotFound
			}
			return err
		}
		if err := requirePartnerOwner(tx, uid, job.TenantID); err != nil {
			return err
		}
		return fn(tx, &job)
	})
	return &job, err
}

func (s *PartnerProvisioningService) Get(ctx context.Context, key string) (*types.PartnerProvisioningJob, error) {
	return s.withJob(ctx, key, func(_ *gorm.DB, _ *types.PartnerProvisioningJob) error { return nil })
}

func (s *PartnerProvisioningService) Lease(ctx context.Context, key, holder string, release bool) (*types.PartnerProvisioningJob, error) {
	if _, err := uuid.Parse(holder); err != nil {
		return nil, ErrPartnerInvalid
	}
	return s.withJob(ctx, key, func(tx *gorm.DB, job *types.PartnerProvisioningJob) error {
		now := s.now().UTC()
		if job.LeaseHolder != "" && job.LeaseHolder != holder && job.LeaseUntil != nil && job.LeaseUntil.After(now) {
			return ErrPartnerConflict
		}
		until := now.Add(5 * time.Minute)
		job.LeaseHolder, job.LeaseUntil = holder, &until
		if release {
			job.LeaseHolder, job.LeaseUntil = "", nil
		}
		return tx.Save(job).Error
	})
}

func (s *PartnerProvisioningService) checkLease(job *types.PartnerProvisioningJob, holder string, revision uint64) error {
	if holder == "" || job.LeaseHolder != holder || job.LeaseUntil == nil || !job.LeaseUntil.After(s.now()) || job.Revision != revision {
		return ErrPartnerConflict
	}
	return nil
}

func unchangedPartnerID(old, next string) bool { return old == "" || old == next }

func (s *PartnerProvisioningService) Update(ctx context.Context, key string, in PartnerProvisioningUpdate) (*types.PartnerProvisioningJob, error) {
	return s.withJob(ctx, key, func(tx *gorm.DB, job *types.PartnerProvisioningJob) error {
		if err := s.checkLease(job, in.LeaseHolder, in.Revision); err != nil {
			return err
		}
		if in.Status != "provisioning" && in.Status != "blocked" && in.Status != "ready" {
			return ErrPartnerInvalid
		}
		if len(in.State.RestrictedSkills) > 0 || in.State.ContributorUserID != job.State.ContributorUserID || !unchangedPartnerID(job.State.OrganizationID, in.State.OrganizationID) || !unchangedPartnerID(job.State.KnowledgeBaseID, in.State.KnowledgeBaseID) {
			return ErrPartnerConflict
		}
		for k, v := range job.State.AgentIDs {
			if in.State.AgentIDs[k] != v {
				return ErrPartnerConflict
			}
		}
		for k, v := range job.State.SkillCatalogIDs {
			if in.State.SkillCatalogIDs[k] != v {
				return ErrPartnerConflict
			}
		}
		in.State.RestrictedSkills = job.State.RestrictedSkills
		if err := validatePartnerState(tx, job, in.State, in.Status == "ready"); err != nil {
			return err
		}
		job.State, job.Status = in.State, in.Status
		job.Revision++
		return tx.Save(job).Error
	})
}

func validatePartnerState(tx *gorm.DB, job *types.PartnerProvisioningJob, state types.PartnerProvisioningState, ready bool) error {
	if len(state.AgentIDs) > 3 || len(state.SkillCatalogIDs) > 9 {
		return ErrPartnerInvalid
	}
	if state.OrganizationID != "" {
		var org types.Organization
		if err := tx.Where("id = ? AND owner_tenant_id = ? AND owner_id = ?", state.OrganizationID, job.HQTenantID, job.OwnerUserID).First(&org).Error; err != nil {
			return ErrPartnerInvalid
		}
		if ready && (org.Searchable || !org.RequireApproval || org.MemberLimit != 2) {
			return ErrPartnerInvalid
		}
		if ready {
			var members []types.OrganizationTenantMember
			if err := tx.Where("organization_id = ?", org.ID).Find(&members).Error; err != nil {
				return err
			}
			if len(members) != 2 {
				return ErrPartnerInvalid
			}
			partnerFound := false
			for _, member := range members {
				if member.TenantID == job.TenantID && member.Role == types.OrgRoleViewer {
					partnerFound = true
				} else if member.TenantID != job.HQTenantID {
					return ErrPartnerInvalid
				}
			}
			if !partnerFound {
				return ErrPartnerInvalid
			}
			for _, kbID := range job.HQKnowledgeBaseIDs {
				var count int64
				if err := tx.Model(&types.KnowledgeBaseShare{}).Where("organization_id = ? AND knowledge_base_id = ? AND source_tenant_id = ? AND permission = ?", org.ID, kbID, job.HQTenantID, types.OrgRoleViewer).Count(&count).Error; err != nil {
					return err
				}
				if count != 1 {
					return ErrPartnerInvalid
				}
			}
		}
	}
	if state.KnowledgeBaseID != "" {
		var kb types.KnowledgeBase
		if err := tx.Where("id = ? AND tenant_id = ?", state.KnowledgeBaseID, job.TenantID).First(&kb).Error; err != nil {
			return ErrPartnerInvalid
		}
		if ready {
			if state.ContributorUserID == "" || kb.CreatorID != state.ContributorUserID {
				return ErrPartnerInvalid
			}
			var member types.TenantMember
			if err := tx.Where("tenant_id = ? AND user_id = ? AND role = ? AND status = ?", job.TenantID, state.ContributorUserID, types.TenantRoleContributor, types.TenantMemberStatusActive).First(&member).Error; err != nil {
				return ErrPartnerInvalid
			}
		}
	}
	agentIDs := map[string]bool{}
	for key, id := range state.AgentIDs {
		if key != "mentor" && key != "planner" && key != "reviewer" {
			return ErrPartnerInvalid
		}
		if agentIDs[id] {
			return ErrPartnerInvalid
		}
		agentIDs[id] = true
		var agent types.CustomAgent
		if err := tx.Where("id = ? AND tenant_id = ? AND created_by = ? AND is_builtin = ?", id, job.TenantID, job.OwnerUserID, false).First(&agent).Error; err != nil {
			return ErrPartnerInvalid
		}
		if agent.Config.SandboxConfigID != "" || agent.Config.SkillsSelectionMode != "none" || !agent.Config.BoyanHybridSearchWithoutRerank {
			return ErrPartnerInvalid
		}
		if agent.Config.KBSelectionMode != "selected" || agent.Config.MCPSelectionMode != "none" || len(agent.Config.MCPServices) != 0 {
			return ErrPartnerInvalid
		}
		allowedKBs := map[string]bool{}
		for _, kbID := range job.HQKnowledgeBaseIDs {
			allowedKBs[kbID] = true
		}
		if key != "mentor" {
			allowedKBs[state.KnowledgeBaseID] = true
		}
		if len(agent.Config.KnowledgeBases) != len(allowedKBs) {
			return ErrPartnerInvalid
		}
		seenKBs := map[string]bool{}
		for _, kbID := range agent.Config.KnowledgeBases {
			if kbID == "" || !allowedKBs[kbID] || seenKBs[kbID] {
				return ErrPartnerInvalid
			}
			seenKBs[kbID] = true
		}
		for _, tool := range agent.Config.AllowedTools {
			if tool != "search_knowledge" && tool != "read_document" && tool != "list_documents" && !(key == "planner" && tool == "boyan_document") {
				return ErrPartnerInvalid
			}
		}
	}
	for name, id := range state.SkillCatalogIDs {
		if _, err := boyanskills.Fixed(name); err != nil {
			return ErrPartnerInvalid
		}
		var cat types.TenantSkillCatalogEntity
		if err := tx.Where("id = ? AND tenant_id = ? AND name = ?", id, job.TenantID, name).First(&cat).Error; err != nil {
			return ErrPartnerInvalid
		}
	}
	if ready {
		if state.OrganizationID == "" || state.KnowledgeBaseID == "" || len(state.AgentIDs) != 3 || len(state.RestrictedSkills) != 9 || len(state.SkillCatalogIDs) != 9 {
			return ErrPartnerInvalid
		}
		var count int64
		if err := tx.Model(&types.TenantSandboxConfigEntity{}).Where("tenant_id = ? AND name = ?", job.TenantID, types.SandboxWorkspacePolicyConfigName).Count(&count).Error; err != nil {
			return err
		}
		if count != 1 {
			return ErrPartnerInvalid
		}
		for name, skill := range state.RestrictedSkills {
			if !skill.Enabled || state.SkillCatalogIDs[name] != skill.CatalogID {
				return ErrPartnerInvalid
			}
			if err := boyanskills.ValidateFixedSkill(name, skill.Version, skill.SHA256); err != nil {
				return ErrPartnerInvalid
			}
		}
	}
	return nil
}

func (s *PartnerProvisioningService) AssignKnowledgeCreator(ctx context.Context, key, holder string, revision uint64, userID string) (*types.PartnerProvisioningJob, error) {
	return s.withJob(ctx, key, func(tx *gorm.DB, job *types.PartnerProvisioningJob) error {
		if err := s.checkLease(job, holder, revision); err != nil {
			return err
		}
		if job.State.KnowledgeBaseID == "" || userID == "" {
			return ErrPartnerInvalid
		}
		var member types.TenantMember
		if err := tx.Where("tenant_id = ? AND user_id = ? AND role = ? AND status = ?", job.TenantID, userID, types.TenantRoleContributor, types.TenantMemberStatusActive).First(&member).Error; err != nil {
			return ErrPartnerInvalid
		}
		var user types.User
		if err := tx.Where("id = ? AND is_active = ?", userID, true).First(&user).Error; err != nil {
			return ErrPartnerInvalid
		}
		var kb types.KnowledgeBase
		if err := tx.Where("id = ? AND tenant_id = ?", job.State.KnowledgeBaseID, job.TenantID).First(&kb).Error; err != nil {
			return ErrPartnerInvalid
		}
		if kb.CreatorID != job.OwnerUserID && kb.CreatorID != userID {
			return ErrPartnerConflict
		}
		if err := tx.Model(&kb).Update("creator_id", userID).Error; err != nil {
			return err
		}
		job.State.ContributorUserID = userID
		job.Revision++
		return tx.Save(job).Error
	})
}

func (s *PartnerProvisioningService) InstallRestrictedSkills(ctx context.Context, key, holder string, revision uint64) (*types.PartnerProvisioningJob, error) {
	return s.withJob(ctx, key, func(tx *gorm.DB, job *types.PartnerProvisioningJob) error {
		if err := s.checkLease(job, holder, revision); err != nil {
			return err
		}
		if len(job.State.SkillCatalogIDs) != 9 {
			return ErrPartnerInvalid
		}
		installed := map[string]types.PartnerRestrictedSkill{}
		for name, id := range job.State.SkillCatalogIDs {
			guide, err := boyanskills.Fixed(name)
			if err != nil {
				return ErrPartnerInvalid
			}
			var cat types.TenantSkillCatalogEntity
			if err := tx.Where("id = ? AND tenant_id = ? AND name = ?", id, job.TenantID, name).First(&cat).Error; err != nil {
				return ErrPartnerInvalid
			}
			if cat.Version != guide.Version || strings.TrimSpace(cat.Instructions) != strings.TrimSpace(guide.Instructions) {
				return ErrPartnerInvalid
			}
			installed[name] = types.PartnerRestrictedSkill{Version: guide.Version, SHA256: guide.SHA256, CatalogID: id, CatalogSHA256: cat.BundleSHA256, Enabled: true}
		}
		job.State.RestrictedSkills = installed
		job.Revision++
		return tx.Save(job).Error
	})
}
