package businessworkflow

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/stretchr/testify/require"
)

func TestHeadquartersWorkflowUsesLocalSourcesWithoutOrganization(t *testing.T) {
	s, ctx, p := fixture(t)
	updateSkillProvision(t, s, func(job *types.PartnerProvisioningJob) {
		job.HQTenantID = job.TenantID
		job.HQKnowledgeBaseIDs = []string{"kb"}
		job.State.KnowledgeBaseID = "working-kb"
		job.State.ContributorUserID = job.OwnerUserID
	})
	require.NoError(t, s.DB.Exec("INSERT INTO knowledge_bases(id,tenant_id) VALUES ('working-kb',1),('unselected-kb',1)").Error)
	require.NoError(t, s.DB.Exec("INSERT INTO knowledges(id,tenant_id,knowledge_base_id,title) VALUES ('working-knowledge',1,'working-kb','总部工作资料'),('unselected-knowledge',1,'unselected-kb','未授权资料')").Error)
	require.NoError(t, s.DB.Exec("INSERT INTO chunks(id,tenant_id,knowledge_id,knowledge_base_id,is_enabled,content) VALUES ('working-chunk',1,'working-knowledge','working-kb',true,'工作说明'),('unselected-chunk',1,'unselected-knowledge','unselected-kb',true,'未授权资料')").Error)
	var planner types.CustomAgent
	require.NoError(t, s.DB.Where("id = ?", "planner").First(&planner).Error)
	planner.Config.KnowledgeBases = []string{"kb", "working-kb"}
	require.NoError(t, s.DB.Save(&planner).Error)
	ctx = types.WithCaller(ctx, types.Caller{TenantID: 1, UserID: "user", Role: types.TenantRoleOwner})
	p.Sources = []SourceRequest{{ChunkID: "chunk"}, {ChunkID: "working-chunk"}}
	job, err := s.Propose(ctx, "session", p)
	require.NoError(t, err)
	var payload Payload
	require.NoError(t, json.Unmarshal(job.Payload, &payload))
	require.Len(t, payload.VerifiedSources, 2)
	require.Equal(t, "headquarters", payload.VerifiedSources[0].Authority)
	require.Equal(t, "partner", payload.VerifiedSources[1].Authority)
	_, err = s.source(ctx, "unselected-chunk")
	require.ErrorIs(t, err, ErrDenied)
	_, err = s.Generate(ctx, "session", job.ID, job.OutlineHash)
	require.ErrorIs(t, err, ErrConflict)
	_, err = s.Confirm(ctx, "session", job.ID, job.OutlineHash)
	require.NoError(t, err)
	require.NoError(t, s.DB.Model(&types.Knowledge{}).Where("id = ?", "knowledge").UpdateColumn("tenant_id", 2).Error)
	_, err = s.Generate(ctx, "session", job.ID, job.OutlineHash)
	require.ErrorIs(t, err, ErrDenied)
}

func TestHeadquartersSkillsRequireReadyInstallationAndFixedHash(t *testing.T) {
	s, ctx := skillCatalogFixture(t)
	updateSkillProvision(t, s, func(job *types.PartnerProvisioningJob) {
		job.HQTenantID = job.TenantID
		job.State.ContributorUserID = job.OwnerUserID
	})
	require.NoError(t, s.DB.Model(&types.TenantMember{}).Where("tenant_id = ? AND user_id = ?", 1, "user").Update("role", types.TenantRoleOwner).Error)
	ctx = types.WithCaller(ctx, types.Caller{TenantID: 1, UserID: "user", Role: types.TenantRoleOwner})
	catalog, err := s.SkillCatalog(ctx)
	require.NoError(t, err)
	require.True(t, catalog.Available)
	require.Len(t, catalog.Skills, 9)
	for _, item := range catalog.Skills {
		detail, err := s.SkillDetail(ctx, item.Name)
		require.NoError(t, err)
		require.Equal(t, item.SHA256, digest([]byte(detail.Content)))
	}
	updateSkillProvision(t, s, func(job *types.PartnerProvisioningJob) { job.Status = "provisioning" })
	_, err = s.SkillDetail(ctx, "boyan-docx")
	require.ErrorIs(t, err, ErrDenied)
	updateSkillProvision(t, s, func(job *types.PartnerProvisioningJob) {
		job.Status = "ready"
		skill := job.State.RestrictedSkills["boyan-docx"]
		skill.SHA256 = strings.Repeat("0", 64)
		job.State.RestrictedSkills["boyan-docx"] = skill
	})
	_, err = s.SkillDetail(ctx, "boyan-docx")
	require.ErrorIs(t, err, ErrDenied)
	other := types.WithCaller(context.Background(), types.Caller{TenantID: 2, UserID: "user", Role: types.TenantRoleOwner})
	other = types.WithPrincipal(other, types.Principal{Type: types.PrincipalWebUser, ID: "user"})
	_, err = s.SkillCatalog(other)
	require.ErrorIs(t, err, ErrDenied)
}
