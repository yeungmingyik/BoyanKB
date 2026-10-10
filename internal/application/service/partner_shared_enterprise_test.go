package service

import (
	"context"
	"strings"
	"testing"

	"github.com/Tencent/WeKnora/internal/boyanskills"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func readySharedEnterprise(t *testing.T, headquarters bool) (*PartnerProvisioningService, *gorm.DB, context.Context, *types.PartnerProvisioningJob) {
	t.Helper()
	svc, db, ctx, in := partnerTestService(t)
	require.NoError(t, db.AutoMigrate(&types.Organization{}, &types.OrganizationTenantMember{}, &types.KnowledgeBaseShare{}))
	in.KnowledgeMode = types.PartnerKnowledgeSharedEnterprise
	if headquarters {
		in.TargetTenantID = in.HQTenantID
		partnerDisabledPolicy(t, db, in.HQTenantID)
	}
	job, _, err := svc.Provision(ctx, in)
	require.NoError(t, err)
	holder := uuid.NewString()
	job, err = svc.Lease(ctx, job.PartnerKey, holder, false)
	require.NoError(t, err)
	state := job.State
	state.AgentIDs, state.SkillCatalogIDs = map[string]string{}, map[string]string{}
	if !headquarters {
		org := types.Organization{ID: uuid.NewString(), Name: "Enterprise reader", OwnerID: job.OwnerUserID, OwnerTenantID: job.HQTenantID, RequireApproval: true, MemberLimit: 2, InviteCode: uuid.NewString()}
		require.NoError(t, db.Create(&org).Error)
		for _, member := range []types.OrganizationTenantMember{
			{ID: uuid.NewString(), OrganizationID: org.ID, TenantID: job.HQTenantID, Role: types.OrgRoleAdmin},
			{ID: uuid.NewString(), OrganizationID: org.ID, TenantID: job.TenantID, Role: types.OrgRoleViewer},
		} {
			require.NoError(t, db.Create(&member).Error)
		}
		require.NoError(t, db.Create(&types.KnowledgeBaseShare{ID: uuid.NewString(), KnowledgeBaseID: in.HQKnowledgeBaseIDs[0], OrganizationID: org.ID, SourceTenantID: job.HQTenantID, Permission: types.OrgRoleViewer}).Error)
		state.OrganizationID = org.ID
	}
	for _, key := range []string{"mentor", "planner", "reviewer"} {
		tools := []string{"search_knowledge", "read_document", "list_documents"}
		if key == "planner" {
			tools = append(tools, "boyan_document")
		}
		agent := types.CustomAgent{ID: uuid.NewString(), TenantID: job.TenantID, CreatedBy: job.OwnerUserID, Name: key, Config: types.CustomAgentConfig{KBSelectionMode: "selected", KnowledgeBases: append([]string{}, in.HQKnowledgeBaseIDs...), MCPSelectionMode: "none", SkillsSelectionMode: "none", AllowedTools: tools, BoyanHybridSearchWithoutRerank: true}}
		require.NoError(t, db.Create(&agent).Error)
		state.AgentIDs[key] = agent.ID
	}
	for _, name := range []string{"boyan-courseware", "boyan-event-plan", "boyan-local-promotion-plan", "boyan-cooperation-proposal", "boyan-faq-guide", "boyan-docx", "boyan-pptx", "boyan-xlsx", "boyan-md"} {
		guide, err := boyanskills.Fixed(name)
		require.NoError(t, err)
		catalog := types.TenantSkillCatalogEntity{ID: uuid.NewString(), TenantID: job.TenantID, Name: name, Version: guide.Version, Instructions: guide.Instructions, BundleSHA256: strings.Repeat("a", 64)}
		require.NoError(t, db.Create(&catalog).Error)
		state.SkillCatalogIDs[name] = catalog.ID
	}
	job, err = svc.Update(ctx, job.PartnerKey, PartnerProvisioningUpdate{LeaseHolder: holder, Revision: job.Revision, Status: "provisioning", State: state})
	require.NoError(t, err)
	job, err = svc.InstallRestrictedSkills(ctx, job.PartnerKey, holder, job.Revision)
	require.NoError(t, err)
	state = job.State
	state.RestrictedSkills = nil
	job, err = svc.Update(ctx, job.PartnerKey, PartnerProvisioningUpdate{LeaseHolder: holder, Revision: job.Revision, Status: "ready", State: state})
	require.NoError(t, err)
	return svc, db, ctx, job
}

func TestPartnerSharedEnterpriseReadyWithoutPrivateKnowledgeOrCreator(t *testing.T) {
	for _, headquarters := range []bool{false, true} {
		t.Run(map[bool]string{false: "partner", true: "headquarters"}[headquarters], func(t *testing.T) {
			svc, db, ctx, job := readySharedEnterprise(t, headquarters)
			require.Equal(t, "ready", job.Status)
			require.Equal(t, types.PartnerKnowledgeSharedEnterprise, job.State.KnowledgeMode)
			require.Empty(t, job.State.KnowledgeBaseID)
			require.Empty(t, job.State.ContributorUserID)
			var sources []types.KnowledgeBase
			require.NoError(t, db.Find(&sources).Error)
			require.Len(t, sources, 1)
			require.Empty(t, sources[0].CreatorID)
			_, err := svc.AssignKnowledgeCreator(ctx, job.PartnerKey, job.LeaseHolder, job.Revision, job.OwnerUserID)
			require.ErrorIs(t, err, ErrPartnerInvalid)
			var source types.KnowledgeBase
			require.NoError(t, db.First(&source, "id = ?", sources[0].ID).Error)
			require.Equal(t, sources[0], source)
			state := job.State
			state.RestrictedSkills = nil
			repeated, err := svc.Update(ctx, job.PartnerKey, PartnerProvisioningUpdate{LeaseHolder: job.LeaseHolder, Revision: job.Revision, Status: "ready", State: state})
			require.NoError(t, err)
			require.Equal(t, job.State, repeated.State)
		})
	}
}

func TestPartnerSharedEnterpriseRequiresExplicitImmutableModeAndSingleSource(t *testing.T) {
	for _, mutation := range []string{"unknown", "multiple", "empty"} {
		t.Run(mutation, func(t *testing.T) {
			svc, db, ctx, in := partnerTestService(t)
			in.KnowledgeMode = types.PartnerKnowledgeSharedEnterprise
			switch mutation {
			case "unknown":
				in.KnowledgeMode = "all_tenants"
			case "multiple":
				in.HQKnowledgeBaseIDs = append(in.HQKnowledgeBaseIDs, uuid.NewString())
			case "empty":
				in.HQKnowledgeBaseIDs = nil
			}
			_, _, err := svc.Provision(ctx, in)
			require.ErrorIs(t, err, ErrPartnerInvalid)
			var count int64
			require.NoError(t, db.Model(&types.PartnerProvisioningJob{}).Count(&count).Error)
			require.Zero(t, count)
		})
	}
	svc, _, ctx, in := partnerTestService(t)
	legacy, _, err := svc.Provision(ctx, in)
	require.NoError(t, err)
	in.KnowledgeMode = types.PartnerKnowledgeSharedEnterprise
	_, _, err = svc.Provision(ctx, in)
	require.ErrorIs(t, err, ErrPartnerConflict)
	holder := uuid.NewString()
	legacy, err = svc.Lease(ctx, legacy.PartnerKey, holder, false)
	require.NoError(t, err)
	state := legacy.State
	state.KnowledgeMode = types.PartnerKnowledgeSharedEnterprise
	_, err = svc.Update(ctx, legacy.PartnerKey, PartnerProvisioningUpdate{LeaseHolder: holder, Revision: legacy.Revision, Status: "provisioning", State: state})
	require.ErrorIs(t, err, ErrPartnerConflict)
}

func TestPartnerSharedEnterpriseRejectsScopeDrift(t *testing.T) {
	for _, mutation := range []string{"private", "source-alias", "contributor", "mode", "extra-source", "agent-extra-kb", "agent-no-source", "source-owner", "organization-editor"} {
		t.Run(mutation, func(t *testing.T) {
			_, db, _, job := readySharedEnterprise(t, false)
			state := job.State
			switch mutation {
			case "private":
				state.KnowledgeBaseID = uuid.NewString()
			case "source-alias":
				state.KnowledgeBaseID = job.HQKnowledgeBaseIDs[0]
			case "contributor":
				state.ContributorUserID = job.OwnerUserID
			case "mode":
				state.KnowledgeMode = ""
			case "extra-source":
				job.HQKnowledgeBaseIDs = append(job.HQKnowledgeBaseIDs, uuid.NewString())
			case "agent-extra-kb", "agent-no-source":
				var agent types.CustomAgent
				require.NoError(t, db.First(&agent, "id = ?", state.AgentIDs["planner"]).Error)
				if mutation == "agent-extra-kb" {
					agent.Config.KnowledgeBases = append(agent.Config.KnowledgeBases, uuid.NewString())
				} else {
					agent.Config.KnowledgeBases = nil
				}
				require.NoError(t, db.Save(&agent).Error)
			case "source-owner":
				require.NoError(t, db.Model(&types.KnowledgeBase{}).Where("id = ?", job.HQKnowledgeBaseIDs[0]).Update("tenant_id", job.TenantID).Error)
			case "organization-editor":
				require.NoError(t, db.Model(&types.OrganizationTenantMember{}).Where("organization_id = ? AND tenant_id = ?", state.OrganizationID, job.TenantID).Update("role", types.OrgRoleEditor).Error)
			}
			require.ErrorIs(t, validatePartnerState(db, job, state, true), ErrPartnerInvalid)
		})
	}
}

func TestPartnerSharedEnterpriseRejectsAlternativeWritableShare(t *testing.T) {
	for _, grant := range []types.OrgMemberRole{types.OrgRoleViewer, types.OrgRoleEditor} {
		t.Run(string(grant), func(t *testing.T) {
			_, db, _, job := readySharedEnterprise(t, false)
			org := types.Organization{ID: uuid.NewString(), Name: "Other sharing path", OwnerID: job.OwnerUserID, OwnerTenantID: job.HQTenantID, InviteCode: uuid.NewString()}
			require.NoError(t, db.Create(&org).Error)
			require.NoError(t, db.Create(&types.OrganizationTenantMember{ID: uuid.NewString(), OrganizationID: org.ID, TenantID: job.HQTenantID, Role: types.OrgRoleAdmin}).Error)
			require.NoError(t, db.Create(&types.OrganizationTenantMember{ID: uuid.NewString(), OrganizationID: org.ID, TenantID: job.TenantID, Role: types.OrgRoleEditor}).Error)
			require.NoError(t, db.Create(&types.KnowledgeBaseShare{ID: uuid.NewString(), KnowledgeBaseID: job.HQKnowledgeBaseIDs[0], OrganizationID: org.ID, SourceTenantID: job.HQTenantID, Permission: grant}).Error)
			err := validatePartnerState(db, job, job.State, true)
			if grant == types.OrgRoleViewer {
				require.NoError(t, err)
			} else {
				require.ErrorIs(t, err, ErrPartnerInvalid)
				require.NoError(t, db.Where("organization_id = ? AND tenant_id = ?", org.ID, job.HQTenantID).Delete(&types.OrganizationTenantMember{}).Error)
				require.NoError(t, validatePartnerState(db, job, job.State, true))
			}
		})
	}
}
