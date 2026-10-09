package service

import (
	"context"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/Tencent/WeKnora/internal/boyanskills"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

func partnerTestService(t *testing.T) (*PartnerProvisioningService, *gorm.DB, context.Context, PartnerProvisioningInput) {
	t.Helper()
	t.Setenv("STORAGE_TYPE", "local")
	t.Setenv("STORAGE_LOCAL_BASE_PATH", "/tmp/partner-test")
	t.Setenv("BOYAN_PRESETS_ROOT", filepath.Join("..", "..", "..", "presets", "boyan", "v1"))
	t.Setenv("BOYAN_SKILLS_ROOT", filepath.Join("..", "..", "..", "skills", "boyan"))
	db, err := gorm.Open(sqlite.Open("file:"+uuid.NewString()+"?mode=memory&cache=shared"), &gorm.Config{DisableForeignKeyConstraintWhenMigrating: true, Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	require.NoError(t, db.AutoMigrate(&types.User{}, &types.Tenant{}, &types.TenantMember{}, &types.KnowledgeBase{}, &types.StorageBackend{}, &types.TenantSandboxConfigEntity{}, &types.PartnerProvisioningJob{}, &types.CustomAgent{}, &types.TenantSkillCatalogEntity{}))
	sqlDB, err := db.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = sqlDB.Close() })
	owner := types.User{ID: uuid.NewString(), Username: "operator", Email: "operator@example.test", PasswordHash: "test", IsActive: true, IsSystemAdmin: true}
	require.NoError(t, db.Create(&owner).Error)
	hq := types.Tenant{Name: "HQ", Status: "active"}
	require.NoError(t, db.Create(&hq).Error)
	require.NoError(t, db.Create(&types.TenantMember{UserID: owner.ID, TenantID: hq.ID, Role: types.TenantRoleOwner, Status: types.TenantMemberStatusActive}).Error)
	kb := types.KnowledgeBase{ID: uuid.NewString(), TenantID: hq.ID, Name: "HQ KB"}
	require.NoError(t, db.Create(&kb).Error)
	ctx := context.WithValue(context.Background(), types.UserIDContextKey, owner.ID)
	ctx = context.WithValue(ctx, types.TenantIDContextKey, hq.ID)
	in := PartnerProvisioningInput{PartnerKey: "partner-test", Name: "Partner", HQTenantID: hq.ID, HQKnowledgeBaseIDs: []string{kb.ID}, SpecHash: strings.Repeat("a", 64)}
	return NewPartnerProvisioningService(db, nil, nil), db, ctx, in
}

func TestPartnerProvisioningCreatesOwnedDisabledTenantAndReplays(t *testing.T) {
	svc, db, ctx, in := partnerTestService(t)
	job, created, err := svc.Provision(ctx, in)
	require.NoError(t, err)
	require.True(t, created)
	uid, _ := types.UserIDFromContext(ctx)
	var member types.TenantMember
	require.NoError(t, db.Where("tenant_id = ? AND user_id = ?", job.TenantID, uid).First(&member).Error)
	require.Equal(t, types.TenantRoleOwner, member.Role)
	var policy types.TenantSandboxConfigEntity
	require.NoError(t, db.Where("tenant_id = ? AND name = ?", job.TenantID, types.SandboxWorkspacePolicyConfigName).First(&policy).Error)
	require.Equal(t, "disabled", policy.SandboxType)
	var tenant types.Tenant
	require.NoError(t, db.First(&tenant, job.TenantID).Error)
	require.NotNil(t, tenant.DefaultStorageBackendID)
	var user types.User
	require.NoError(t, db.First(&user, "id = ?", uid).Error)
	require.False(t, user.CanAccessAllTenants)
	second, created, err := svc.Provision(ctx, in)
	require.NoError(t, err)
	require.False(t, created)
	require.Equal(t, job.TenantID, second.TenantID)
	var count int64
	require.NoError(t, db.Model(&types.Tenant{}).Count(&count).Error)
	require.Equal(t, int64(2), count)
	in.SpecHash = strings.Repeat("b", 64)
	_, _, err = svc.Provision(ctx, in)
	require.ErrorIs(t, err, ErrPartnerConflict)
}

func TestPartnerProvisioningRequiresLiveAdminAndHQOwner(t *testing.T) {
	for _, mutation := range []string{"admin", "active", "owner", "foreign-kb", "api-key"} {
		t.Run(mutation, func(t *testing.T) {
			svc, db, ctx, in := partnerTestService(t)
			uid, _ := types.UserIDFromContext(ctx)
			switch mutation {
			case "admin":
				require.NoError(t, db.Model(&types.User{}).Where("id = ?", uid).Update("is_system_admin", false).Error)
			case "active":
				require.NoError(t, db.Model(&types.User{}).Where("id = ?", uid).Update("is_active", false).Error)
			case "owner":
				require.NoError(t, db.Model(&types.TenantMember{}).Where("user_id = ?", uid).Update("role", "admin").Error)
			case "foreign-kb":
				in.HQKnowledgeBaseIDs = []string{uuid.NewString()}
			case "api-key":
				ctx = context.WithValue(ctx, types.TenantAPIKeyScopeContextKey, types.TenantAPIKeyScope{FullAccess: true})
			}
			_, _, err := svc.Provision(ctx, in)
			require.Error(t, err)
			var count int64
			require.NoError(t, db.Model(&types.Tenant{}).Count(&count).Error)
			require.Equal(t, int64(1), count)
		})
	}
}

func TestPartnerProvisioningRollsBackTenantAndOwner(t *testing.T) {
	svc, db, ctx, in := partnerTestService(t)
	require.NoError(t, db.Exec("CREATE TRIGGER fail_partner_job BEFORE INSERT ON partner_provisioning_jobs BEGIN SELECT RAISE(ABORT, 'fixture failure'); END").Error)
	_, _, err := svc.Provision(ctx, in)
	require.Error(t, err)
	for _, table := range []string{"tenants", "tenant_members"} {
		var count int64
		require.NoError(t, db.Table(table).Count(&count).Error)
		require.Equal(t, int64(1), count)
	}
	for _, table := range []string{"storage_backends", "tenant_sandbox_configs", "partner_provisioning_jobs"} {
		var count int64
		require.NoError(t, db.Table(table).Count(&count).Error)
		require.Zero(t, count)
	}
}

func TestPartnerProvisioningLeaseAndRevisionRejectRacingWriters(t *testing.T) {
	svc, _, ctx, in := partnerTestService(t)
	_, _, err := svc.Provision(ctx, in)
	require.NoError(t, err)
	holder := uuid.NewString()
	job, err := svc.Lease(ctx, in.PartnerKey, holder, false)
	require.NoError(t, err)
	_, err = svc.Lease(ctx, in.PartnerKey, uuid.NewString(), false)
	require.ErrorIs(t, err, ErrPartnerConflict)
	input := PartnerProvisioningUpdate{LeaseHolder: holder, Revision: job.Revision, Status: "provisioning"}
	_, err = svc.Update(ctx, in.PartnerKey, input)
	require.NoError(t, err)
	_, err = svc.Update(ctx, in.PartnerKey, input)
	require.ErrorIs(t, err, ErrPartnerConflict)
	svc.now = func() time.Time { return time.Now().Add(6 * time.Minute) }
	_, err = svc.Update(ctx, in.PartnerKey, input)
	require.ErrorIs(t, err, ErrPartnerConflict)
	_, err = svc.Lease(ctx, in.PartnerKey, uuid.NewString(), false)
	require.NoError(t, err)
}

func TestPartnerProvisioningCannotAssignForeignOrViewerCreator(t *testing.T) {
	svc, db, ctx, in := partnerTestService(t)
	job, _, err := svc.Provision(ctx, in)
	require.NoError(t, err)
	uid, _ := types.UserIDFromContext(ctx)
	kb := types.KnowledgeBase{ID: uuid.NewString(), TenantID: job.TenantID, Name: "Partner KB", CreatorID: uid}
	require.NoError(t, db.Create(&kb).Error)
	holder := uuid.NewString()
	job, err = svc.Lease(ctx, in.PartnerKey, holder, false)
	require.NoError(t, err)
	job, err = svc.Update(ctx, in.PartnerKey, PartnerProvisioningUpdate{LeaseHolder: holder, Revision: job.Revision, Status: "provisioning", State: types.PartnerProvisioningState{KnowledgeBaseID: kb.ID}})
	require.NoError(t, err)
	user := types.User{ID: uuid.NewString(), Username: "partner", Email: "partner@example.test", PasswordHash: "test", IsActive: true}
	require.NoError(t, db.Create(&user).Error)
	_, err = svc.AssignKnowledgeCreator(ctx, in.PartnerKey, holder, job.Revision, user.ID)
	require.ErrorIs(t, err, ErrPartnerInvalid)
	member := types.TenantMember{UserID: user.ID, TenantID: job.TenantID, Role: types.TenantRoleViewer, Status: types.TenantMemberStatusActive}
	require.NoError(t, db.Create(&member).Error)
	_, err = svc.AssignKnowledgeCreator(ctx, in.PartnerKey, holder, job.Revision, user.ID)
	require.ErrorIs(t, err, ErrPartnerInvalid)
	require.NoError(t, db.Model(&member).Update("role", "contributor").Error)
	_, err = svc.AssignKnowledgeCreator(ctx, in.PartnerKey, holder, job.Revision, user.ID)
	require.NoError(t, err)
	require.NoError(t, db.First(&kb, "id = ?", kb.ID).Error)
	require.Equal(t, user.ID, kb.CreatorID)
}

func TestPartnerProvisioningRejectsForeignKBAndRevokedPartnerOwner(t *testing.T) {
	svc, db, ctx, in := partnerTestService(t)
	job, _, err := svc.Provision(ctx, in)
	require.NoError(t, err)
	holder := uuid.NewString()
	job, err = svc.Lease(ctx, in.PartnerKey, holder, false)
	require.NoError(t, err)
	_, err = svc.Update(ctx, in.PartnerKey, PartnerProvisioningUpdate{LeaseHolder: holder, Revision: job.Revision, Status: "provisioning", State: types.PartnerProvisioningState{KnowledgeBaseID: in.HQKnowledgeBaseIDs[0]}})
	require.ErrorIs(t, err, ErrPartnerInvalid)
	_, err = svc.Update(ctx, in.PartnerKey, PartnerProvisioningUpdate{LeaseHolder: holder, Revision: job.Revision, Status: "ready"})
	require.ErrorIs(t, err, ErrPartnerInvalid)
	require.NoError(t, db.Model(&types.TenantMember{}).Where("tenant_id = ?", job.TenantID).Update("role", "admin").Error)
	_, err = svc.Get(ctx, in.PartnerKey)
	require.ErrorIs(t, err, ErrPartnerForbidden)
}

func TestPartnerProvisioningRestrictedSkillsVerifyFixedInstructions(t *testing.T) {
	svc, db, ctx, in := partnerTestService(t)
	job, _, err := svc.Provision(ctx, in)
	require.NoError(t, err)
	job.State.SkillCatalogIDs = map[string]string{}
	for _, name := range []string{"boyan-courseware", "boyan-event-plan", "boyan-local-promotion-plan", "boyan-cooperation-proposal", "boyan-faq-guide", "boyan-docx", "boyan-pptx", "boyan-xlsx", "boyan-md"} {
		guide, err := boyanskills.Fixed(name)
		require.NoError(t, err)
		cat := types.TenantSkillCatalogEntity{ID: uuid.NewString(), TenantID: job.TenantID, Name: name, Version: guide.Version, Instructions: guide.Instructions, BundleSHA256: strings.Repeat("a", 64)}
		require.NoError(t, db.Create(&cat).Error)
		job.State.SkillCatalogIDs[name] = cat.ID
	}
	require.NoError(t, db.Save(job).Error)
	holder := uuid.NewString()
	job, err = svc.Lease(ctx, in.PartnerKey, holder, false)
	require.NoError(t, err)
	id := job.State.SkillCatalogIDs["boyan-docx"]
	require.NoError(t, db.Model(&types.TenantSkillCatalogEntity{}).Where("id = ?", id).Update("instructions", "replaced instructions").Error)
	_, err = svc.InstallRestrictedSkills(ctx, in.PartnerKey, holder, job.Revision)
	require.ErrorIs(t, err, ErrPartnerInvalid)
	guide, err := boyanskills.Fixed("boyan-docx")
	require.NoError(t, err)
	require.NoError(t, db.Model(&types.TenantSkillCatalogEntity{}).Where("id = ?", id).Update("instructions", guide.Instructions).Error)
	job, err = svc.InstallRestrictedSkills(ctx, in.PartnerKey, holder, job.Revision)
	require.NoError(t, err)
	require.Len(t, job.State.RestrictedSkills, 9)
	require.Equal(t, guide.SHA256, job.State.RestrictedSkills["boyan-docx"].SHA256)
	require.Equal(t, strings.Repeat("a", 64), job.State.RestrictedSkills["boyan-docx"].CatalogSHA256)
	require.Equal(t, "provisioning", job.Status)
}

func TestPartnerProvisioningAgentBindingRequiresManagedSearch(t *testing.T) {
	svc, db, ctx, in := partnerTestService(t)
	job, _, err := svc.Provision(ctx, in)
	require.NoError(t, err)
	agent := types.CustomAgent{ID: uuid.NewString(), TenantID: job.TenantID, CreatedBy: job.OwnerUserID, Name: "Mentor", Config: types.CustomAgentConfig{KBSelectionMode: "selected", KnowledgeBases: in.HQKnowledgeBaseIDs, MCPSelectionMode: "none", SkillsSelectionMode: "none", AllowedTools: []string{"search_knowledge", "read_document", "list_documents"}}}
	require.NoError(t, db.Create(&agent).Error)
	state := types.PartnerProvisioningState{AgentIDs: map[string]string{"mentor": agent.ID}}
	require.ErrorIs(t, validatePartnerState(db, job, state, false), ErrPartnerInvalid)
	agent.Config.BoyanHybridSearchWithoutRerank = true
	require.NoError(t, db.Save(&agent).Error)
	require.NoError(t, validatePartnerState(db, job, state, false))
	agent.Config.AllowedTools = append(agent.Config.AllowedTools, "execute_code")
	require.NoError(t, db.Save(&agent).Error)
	require.ErrorIs(t, validatePartnerState(db, job, state, false), ErrPartnerInvalid)
}
