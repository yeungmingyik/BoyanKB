package service

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
	"testing"

	"github.com/Tencent/WeKnora/internal/boyanskills"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func partnerDisabledPolicy(t *testing.T, db *gorm.DB, tenantID uint64) {
	t.Helper()
	require.NoError(t, db.Create(&types.TenantSandboxConfigEntity{ID: uuid.NewString(), TenantID: tenantID, Name: types.SandboxWorkspacePolicyConfigName, SandboxType: "disabled", Config: &types.TenantSandboxConfig{SandboxType: "disabled"}}).Error)
}

func TestPartnerProvisioningTargetPreservesLegacyRequestHash(t *testing.T) {
	in := PartnerProvisioningInput{PartnerKey: "partner", Name: "Partner", HQTenantID: 10000, HQKnowledgeBaseIDs: []string{"00000000-0000-0000-0000-000000000001"}, SpecHash: strings.Repeat("a", 64)}
	legacy := `{"partner_key":"partner","name":"Partner","description":"","hq_tenant_id":10000,"hq_knowledge_base_ids":["00000000-0000-0000-0000-000000000001"],"spec_hash":"` + strings.Repeat("a", 64) + `"}`
	data, err := json.Marshal(in)
	require.NoError(t, err)
	require.Equal(t, legacy, string(data))
	hash := sha256.Sum256([]byte(legacy))
	require.Equal(t, hex.EncodeToString(hash[:]), partnerRequestHash(in))
	in.TargetTenantID = in.HQTenantID
	require.NotEqual(t, hex.EncodeToString(hash[:]), partnerRequestHash(in))
}

func TestPartnerProvisioningExistingHQCreatesOnlyJobAndReplays(t *testing.T) {
	svc, db, ctx, in := partnerTestService(t)
	in.TargetTenantID = in.HQTenantID
	partnerDisabledPolicy(t, db, in.HQTenantID)
	var before types.Tenant
	require.NoError(t, db.First(&before, in.HQTenantID).Error)
	job, created, err := svc.Provision(ctx, in)
	require.NoError(t, err)
	require.True(t, created)
	require.Equal(t, in.HQTenantID, job.TenantID)
	require.Empty(t, job.State.OrganizationID)
	var after types.Tenant
	require.NoError(t, db.First(&after, in.HQTenantID).Error)
	require.Equal(t, before, after)
	for table, expected := range map[string]int64{"tenants": 1, "tenant_members": 1, "storage_backends": 0, "tenant_sandbox_configs": 1, "partner_provisioning_jobs": 1} {
		var count int64
		require.NoError(t, db.Table(table).Count(&count).Error)
		require.Equal(t, expected, count, table)
	}
	repeated, created, err := svc.Provision(ctx, in)
	require.NoError(t, err)
	require.False(t, created)
	require.Equal(t, job.PartnerKey, repeated.PartnerKey)
	require.Equal(t, job.RequestHash, repeated.RequestHash)
	require.Equal(t, job.TenantID, repeated.TenantID)
	require.Equal(t, job.Revision, repeated.Revision)
	require.Equal(t, job.State, repeated.State)
	require.True(t, job.CreatedAt.Equal(repeated.CreatedAt))
	require.True(t, job.UpdatedAt.Equal(repeated.UpdatedAt))
	in.PartnerKey = "different-key"
	_, _, err = svc.Provision(ctx, in)
	require.ErrorIs(t, err, ErrPartnerConflict)
	in.PartnerKey = job.PartnerKey
	in.TargetTenantID = 0
	_, _, err = svc.Provision(ctx, in)
	require.ErrorIs(t, err, ErrPartnerConflict)
}

func TestPartnerProvisioningExistingTargetRequiresBothOwnersAndWebAdmin(t *testing.T) {
	for _, mutation := range []string{"allowed", "target-owner", "source-owner", "inactive-target", "inactive-member", "system-admin", "api-key", "embed", "different-principal", "foreign-source"} {
		t.Run(mutation, func(t *testing.T) {
			svc, db, ctx, in := partnerTestService(t)
			uid, _ := types.UserIDFromContext(ctx)
			target := types.Tenant{Name: "Existing", Status: "active"}
			require.NoError(t, db.Create(&target).Error)
			require.NoError(t, db.Create(&types.TenantMember{UserID: uid, TenantID: target.ID, Role: types.TenantRoleOwner, Status: types.TenantMemberStatusActive}).Error)
			partnerDisabledPolicy(t, db, target.ID)
			in.TargetTenantID = target.ID
			switch mutation {
			case "target-owner":
				require.NoError(t, db.Model(&types.TenantMember{}).Where("tenant_id = ?", target.ID).Update("role", types.TenantRoleAdmin).Error)
			case "source-owner":
				require.NoError(t, db.Model(&types.TenantMember{}).Where("tenant_id = ?", in.HQTenantID).Update("role", types.TenantRoleAdmin).Error)
			case "inactive-target":
				require.NoError(t, db.Model(&target).Update("status", "suspended").Error)
			case "inactive-member":
				require.NoError(t, db.Model(&types.TenantMember{}).Where("tenant_id = ?", target.ID).Update("status", "suspended").Error)
			case "system-admin":
				require.NoError(t, db.Model(&types.User{}).Where("id = ?", uid).Update("is_system_admin", false).Error)
			case "api-key":
				ctx = context.WithValue(ctx, types.TenantAPIKeyScopeContextKey, types.TenantAPIKeyScope{FullAccess: true})
			case "embed":
				ctx = types.WithPrincipal(ctx, types.Principal{Type: types.PrincipalEmbedSession, ID: uid})
			case "different-principal":
				ctx = types.WithPrincipal(ctx, types.Principal{Type: types.PrincipalWebUser, ID: uuid.NewString()})
			case "foreign-source":
				kb := types.KnowledgeBase{ID: uuid.NewString(), TenantID: target.ID, Name: "Other source"}
				require.NoError(t, db.Create(&kb).Error)
				in.HQKnowledgeBaseIDs = []string{kb.ID}
			}
			job, created, err := svc.Provision(ctx, in)
			var count int64
			require.NoError(t, db.Model(&types.PartnerProvisioningJob{}).Count(&count).Error)
			if mutation == "allowed" {
				require.NoError(t, err)
				require.True(t, created)
				require.Equal(t, target.ID, job.TenantID)
				require.Equal(t, int64(1), count)
				require.NoError(t, db.Model(&types.TenantMember{}).Where("tenant_id = ?", target.ID).Update("role", types.TenantRoleViewer).Error)
				_, err = svc.Get(ctx, in.PartnerKey)
				require.ErrorIs(t, err, ErrPartnerForbidden)
			} else {
				require.Error(t, err)
				require.Zero(t, count)
			}
			require.NoError(t, db.Model(&types.Tenant{}).Count(&count).Error)
			require.Equal(t, int64(2), count)
		})
	}
}

func TestPartnerProvisioningExistingTargetSandboxIsReadOnlyPrecondition(t *testing.T) {
	for _, mutation := range []string{"missing", "wrong-type", "named-backend", "deleted"} {
		t.Run(mutation, func(t *testing.T) {
			svc, db, ctx, in := partnerTestService(t)
			in.TargetTenantID = in.HQTenantID
			if mutation != "missing" {
				partnerDisabledPolicy(t, db, in.HQTenantID)
			}
			switch mutation {
			case "wrong-type":
				require.NoError(t, db.Model(&types.TenantSandboxConfigEntity{}).Where("tenant_id = ?", in.HQTenantID).Update("sandbox_type", "local").Error)
			case "named-backend":
				require.NoError(t, db.Create(&types.TenantSandboxConfigEntity{ID: uuid.NewString(), TenantID: in.HQTenantID, Name: "existing-runtime", SandboxType: "e2b"}).Error)
			case "deleted":
				require.NoError(t, db.Where("tenant_id = ?", in.HQTenantID).Delete(&types.TenantSandboxConfigEntity{}).Error)
			}
			var before, after []types.TenantSandboxConfigEntity
			require.NoError(t, db.Unscoped().Order("id").Find(&before).Error)
			_, _, err := svc.Provision(ctx, in)
			require.ErrorIs(t, err, ErrPartnerInvalid)
			require.NoError(t, db.Unscoped().Order("id").Find(&after).Error)
			require.Equal(t, before, after)
			var count int64
			require.NoError(t, db.Model(&types.PartnerProvisioningJob{}).Count(&count).Error)
			require.Zero(t, count)
		})
	}
}

func readyHQPartner(t *testing.T) (*PartnerProvisioningService, *gorm.DB, context.Context, *types.PartnerProvisioningJob) {
	t.Helper()
	svc, db, ctx, in := partnerTestService(t)
	in.TargetTenantID = in.HQTenantID
	partnerDisabledPolicy(t, db, in.HQTenantID)
	job, _, err := svc.Provision(ctx, in)
	require.NoError(t, err)
	holder := uuid.NewString()
	job, err = svc.Lease(ctx, in.PartnerKey, holder, false)
	require.NoError(t, err)
	kb := types.KnowledgeBase{ID: uuid.NewString(), TenantID: job.TenantID, Name: "HQ workspace material", CreatorID: job.OwnerUserID}
	require.NoError(t, db.Create(&kb).Error)
	state := types.PartnerProvisioningState{KnowledgeBaseID: kb.ID, AgentIDs: map[string]string{}, SkillCatalogIDs: map[string]string{}}
	for _, key := range []string{"mentor", "planner", "reviewer"} {
		kbs := append([]string{}, in.HQKnowledgeBaseIDs...)
		tools := []string{"search_knowledge", "read_document", "list_documents"}
		if key != "mentor" {
			kbs = append(kbs, kb.ID)
		}
		if key == "planner" {
			tools = append(tools, "boyan_document")
		}
		agent := types.CustomAgent{ID: uuid.NewString(), TenantID: job.TenantID, CreatedBy: job.OwnerUserID, Name: key, Config: types.CustomAgentConfig{KBSelectionMode: "selected", KnowledgeBases: kbs, MCPSelectionMode: "none", SkillsSelectionMode: "none", AllowedTools: tools, BoyanHybridSearchWithoutRerank: true}}
		require.NoError(t, db.Create(&agent).Error)
		state.AgentIDs[key] = agent.ID
	}
	for _, name := range []string{"boyan-courseware", "boyan-event-plan", "boyan-local-promotion-plan", "boyan-cooperation-proposal", "boyan-faq-guide", "boyan-docx", "boyan-pptx", "boyan-xlsx", "boyan-md"} {
		guide, err := boyanskills.Fixed(name)
		require.NoError(t, err)
		cat := types.TenantSkillCatalogEntity{ID: uuid.NewString(), TenantID: job.TenantID, Name: name, Version: guide.Version, Instructions: guide.Instructions, BundleSHA256: strings.Repeat("a", 64)}
		require.NoError(t, db.Create(&cat).Error)
		state.SkillCatalogIDs[name] = cat.ID
	}
	job, err = svc.Update(ctx, job.PartnerKey, PartnerProvisioningUpdate{LeaseHolder: holder, Revision: job.Revision, Status: "provisioning", State: state})
	require.NoError(t, err)
	job, err = svc.AssignKnowledgeCreator(ctx, job.PartnerKey, holder, job.Revision, job.OwnerUserID)
	require.NoError(t, err)
	job, err = svc.InstallRestrictedSkills(ctx, job.PartnerKey, holder, job.Revision)
	require.NoError(t, err)
	state = job.State
	state.RestrictedSkills = nil
	job, err = svc.Update(ctx, job.PartnerKey, PartnerProvisioningUpdate{LeaseHolder: holder, Revision: job.Revision, Status: "ready", State: state})
	require.NoError(t, err)
	return svc, db, ctx, job
}

func TestPartnerProvisioningHQReadyUsesOwnerWithoutSharedOrganization(t *testing.T) {
	_, db, _, job := readyHQPartner(t)
	require.Equal(t, "ready", job.Status)
	require.Equal(t, job.HQTenantID, job.TenantID)
	require.Empty(t, job.State.OrganizationID)
	require.Equal(t, job.OwnerUserID, job.State.ContributorUserID)
	require.Len(t, job.State.AgentIDs, 3)
	require.Len(t, job.State.RestrictedSkills, 9)
	var member types.TenantMember
	require.NoError(t, db.Where("user_id = ? AND tenant_id = ?", job.OwnerUserID, job.TenantID).First(&member).Error)
	require.Equal(t, types.TenantRoleOwner, member.Role)
	content, err := boyanskills.Read(context.Background(), db, job.TenantID, "boyan-courseware")
	require.NoError(t, err)
	require.NotEmpty(t, content)
}

func TestPartnerProvisioningHQReadyStillRejectsScopeAndIntegrityChanges(t *testing.T) {
	for _, mutation := range []string{"organization", "same-source-private-kb", "foreign-private-kb", "foreign-hq-source", "owner-demoted", "owner-disabled", "skill-hash", "foreign-agent", "agent-extra-kb", "scripts-enabled", "non-hq-owner"} {
		t.Run(mutation, func(t *testing.T) {
			_, db, _, job := readyHQPartner(t)
			state := job.State
			switch mutation {
			case "organization":
				state.OrganizationID = uuid.NewString()
			case "same-source-private-kb":
				state.KnowledgeBaseID = job.HQKnowledgeBaseIDs[0]
			case "foreign-private-kb":
				require.NoError(t, db.Model(&types.KnowledgeBase{}).Where("id = ?", state.KnowledgeBaseID).Update("tenant_id", job.TenantID+1).Error)
			case "foreign-hq-source":
				require.NoError(t, db.Model(&types.KnowledgeBase{}).Where("id = ?", job.HQKnowledgeBaseIDs[0]).Update("tenant_id", job.TenantID+1).Error)
			case "owner-demoted":
				require.NoError(t, db.Model(&types.TenantMember{}).Where("user_id = ?", job.OwnerUserID).Update("role", types.TenantRoleViewer).Error)
			case "owner-disabled":
				require.NoError(t, db.Model(&types.User{}).Where("id = ?", job.OwnerUserID).Update("is_active", false).Error)
			case "skill-hash":
				skill := state.RestrictedSkills["boyan-docx"]
				skill.SHA256 = strings.Repeat("0", 64)
				state.RestrictedSkills["boyan-docx"] = skill
			case "foreign-agent":
				require.NoError(t, db.Model(&types.CustomAgent{}).Where("id = ?", state.AgentIDs["planner"]).Update("tenant_id", job.TenantID+1).Error)
			case "agent-extra-kb":
				var agent types.CustomAgent
				require.NoError(t, db.Where("id = ?", state.AgentIDs["planner"]).First(&agent).Error)
				agent.Config.KnowledgeBases = append(agent.Config.KnowledgeBases, uuid.NewString())
				require.NoError(t, db.Save(&agent).Error)
			case "scripts-enabled":
				require.NoError(t, db.Where("tenant_id = ?", job.TenantID).Delete(&types.TenantSandboxConfigEntity{}).Error)
			case "non-hq-owner":
				job.HQTenantID++
				require.ErrorIs(t, requirePartnerKnowledgeCreator(db, job, job.OwnerUserID), ErrPartnerInvalid)
				return
			}
			require.ErrorIs(t, validatePartnerState(db, job, state, true), ErrPartnerInvalid)
		})
	}
}
