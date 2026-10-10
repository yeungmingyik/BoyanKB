package businessworkflow

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/Tencent/WeKnora/internal/application/repository"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/stretchr/testify/require"
)

func teamFixture(t *testing.T) (*Service, context.Context) {
	t.Helper()
	s, ctx := skillCatalogFixture(t)
	require.NoError(t, s.DB.AutoMigrate(&types.User{}, &types.Tenant{}, &types.TenantInvitation{}, &types.AuditLog{}))
	for _, user := range []types.User{
		{ID: "user", Username: "负责人", Email: "manager@example.test", PasswordHash: "test", IsActive: true},
		{ID: "employee", Username: "员工", Email: "employee@example.test", PasswordHash: "test", IsActive: true},
		{ID: "owner", Username: "总部负责人", Email: "owner@example.test", PasswordHash: "test", IsActive: true},
		{ID: "other", Username: "另一空间员工", Email: "other@example.test", PasswordHash: "test", IsActive: true},
		{ID: "new", Username: "待邀请员工", Email: "new@example.test", PasswordHash: "test", IsActive: true},
	} {
		require.NoError(t, s.DB.Create(&user).Error)
	}
	for _, tenant := range []types.Tenant{{ID: 1, Name: "当前伙伴", Status: "active"}, {ID: 2, Name: "其他伙伴", Status: "active"}} {
		require.NoError(t, s.DB.Create(&tenant).Error)
	}
	for _, member := range []types.TenantMember{
		{TenantID: 1, UserID: "employee", Role: types.TenantRoleViewer, Status: types.TenantMemberStatusActive},
		{TenantID: 1, UserID: "owner", Role: types.TenantRoleOwner, Status: types.TenantMemberStatusActive},
		{TenantID: 2, UserID: "other", Role: types.TenantRoleViewer, Status: types.TenantMemberStatusActive},
	} {
		require.NoError(t, s.DB.Create(&member).Error)
	}
	updateSkillProvision(t, s, func(job *types.PartnerProvisioningJob) {
		job.State.KnowledgeMode = types.PartnerKnowledgeSharedEnterprise
		job.State.TeamManagerUserID = "user"
		job.State.KnowledgeBaseID = ""
		job.State.ContributorUserID = ""
		job.HQKnowledgeBaseIDs = types.StringArray{"kb"}
	})
	return s, ctx
}

func teamActor(ctx context.Context, tenant uint64, user string) context.Context {
	ctx = types.WithCaller(ctx, types.Caller{TenantID: tenant, UserID: user, Role: types.TenantRoleViewer})
	return types.WithPrincipal(ctx, types.Principal{Type: types.PrincipalWebUser, ID: user})
}

func TestTeamCapabilityOnlyShowsCurrentViewerRoster(t *testing.T) {
	s, ctx := teamFixture(t)
	result, err := s.Team(ctx)
	require.NoError(t, err)
	require.True(t, result.Available)
	require.Len(t, result.Members, 2)
	for _, member := range result.Members {
		require.Equal(t, types.TenantRoleViewer, member.Role)
		require.Contains(t, []string{"user", "employee"}, member.UserID)
	}
	for _, actor := range []string{"employee", "owner"} {
		result, err := s.Team(teamActor(ctx, 1, actor))
		require.NoError(t, err)
		require.False(t, result.Available)
		require.Empty(t, result.Members)
	}
	serialized, err := json.Marshal(result)
	require.NoError(t, err)
	require.NotContains(t, string(serialized), "email")
	require.NotContains(t, string(serialized), "另一空间")
}

func TestTeamInvitationLinkIsViewerAndSecretReturnedOnce(t *testing.T) {
	s, ctx := teamFixture(t)
	created, err := s.CreateTeamInvitation(ctx, TeamInvitationInput{Kind: "link"})
	require.NoError(t, err)
	require.Len(t, created.Token, 43)
	require.Equal(t, "/register?token="+created.Token, created.InviteURL)
	var stored types.TenantInvitation
	require.NoError(t, s.DB.First(&stored, created.ID).Error)
	require.Equal(t, uint64(1), stored.TenantID)
	require.Equal(t, types.TenantRoleViewer, stored.Role)
	require.Equal(t, "user", stored.TeamManagerUserID)
	listed, err := s.TeamInvitations(ctx)
	require.NoError(t, err)
	require.Equal(t, int64(1), listed.Total)
	encoded, err := json.Marshal(listed)
	require.NoError(t, err)
	require.NotContains(t, string(encoded), created.Token)
	require.NotContains(t, string(encoded), "invite_url")
	var audit types.AuditLog
	require.NoError(t, s.DB.First(&audit).Error)
	require.NotContains(t, string(audit.Details), created.Token)
	require.Equal(t, types.AuditActionInvitationSent, audit.Action)
}

func TestTeamEmailInvitationUsesExistingAccountWithoutMembershipMutation(t *testing.T) {
	s, ctx := teamFixture(t)
	created, err := s.CreateTeamInvitation(ctx, TeamInvitationInput{Kind: "email", Email: "new@example.test"})
	require.NoError(t, err)
	require.Equal(t, "待邀请员工", created.InviteeName)
	require.Empty(t, created.Token)
	require.Empty(t, created.InviteURL)
	var memberships int64
	require.NoError(t, s.DB.Model(&types.TenantMember{}).Where("user_id = ?", "new").Count(&memberships).Error)
	require.Zero(t, memberships)
	_, err = s.CreateTeamInvitation(ctx, TeamInvitationInput{Kind: "email", Email: "new@example.test"})
	require.ErrorIs(t, err, ErrConflict)
	_, err = s.CreateTeamInvitation(ctx, TeamInvitationInput{Kind: "email", Email: "employee@example.test"})
	require.ErrorIs(t, err, ErrConflict)
	_, err = s.CreateTeamInvitation(ctx, TeamInvitationInput{Kind: "email", Email: "unknown@example.test"})
	require.ErrorIs(t, err, ErrInvalid)
	require.NoError(t, s.DB.Model(&types.TenantInvitation{}).Where("id = ?", created.ID).Update("expires_at", time.Now().Add(-time.Hour)).Error)
	replacement, err := s.CreateTeamInvitation(ctx, TeamInvitationInput{Kind: "email", Email: "new@example.test"})
	require.NoError(t, err)
	require.NotEqual(t, created.ID, replacement.ID)
}

func TestTeamRefusesForeignUsersMachinesAndPayloadConfusion(t *testing.T) {
	s, ctx := teamFixture(t)
	for _, other := range []context.Context{
		teamActor(ctx, 2, "user"), teamActor(ctx, 1, "employee"),
		types.WithPrincipal(ctx, types.Principal{Type: types.PrincipalAPITenant, ID: "key"}),
		types.WithPrincipal(ctx, types.Principal{Type: types.PrincipalWebUser, ID: "employee"}),
		context.WithValue(ctx, types.TenantAPIKeyScopeContextKey, types.TenantAPIKeyScope{FullAccess: true}),
	} {
		_, err := s.CreateTeamInvitation(other, TeamInvitationInput{Kind: "link"})
		require.ErrorIs(t, err, ErrDenied)
		_, err = s.TeamInvitations(other)
		require.ErrorIs(t, err, ErrDenied)
	}
	for _, input := range []TeamInvitationInput{{Kind: "owner"}, {Kind: "link", Email: "new@example.test"}, {Kind: "email", Email: "Name <new@example.test>"}} {
		_, err := s.CreateTeamInvitation(ctx, input)
		require.ErrorIs(t, err, ErrInvalid)
	}
}

func TestRevokedManagerCannotCreateOrUsePriorLinkOrEmailInvite(t *testing.T) {
	for _, mutation := range []string{"member", "role", "user", "tenant", "manager", "status", "mode"} {
		t.Run(mutation, func(t *testing.T) {
			s, ctx := teamFixture(t)
			link, err := s.CreateTeamInvitation(ctx, TeamInvitationInput{Kind: "link"})
			require.NoError(t, err)
			email, err := s.CreateTeamInvitation(ctx, TeamInvitationInput{Kind: "email", Email: "new@example.test"})
			require.NoError(t, err)
			repo := repository.NewTenantInvitationRepository(s.DB)
			active, err := repo.GetActiveByToken(ctx, link.Token)
			require.NoError(t, err)
			require.NotNil(t, active)
			switch mutation {
			case "member":
				require.NoError(t, s.DB.Model(&types.TenantMember{}).Where("user_id = ?", "user").Update("status", "suspended").Error)
			case "role":
				require.NoError(t, s.DB.Model(&types.TenantMember{}).Where("user_id = ?", "user").Update("role", "owner").Error)
			case "user":
				require.NoError(t, s.DB.Model(&types.User{}).Where("id = ?", "user").Update("is_active", false).Error)
			case "tenant":
				require.NoError(t, s.DB.Model(&types.Tenant{}).Where("id = ?", 1).Update("status", "inactive").Error)
			default:
				updateSkillProvision(t, s, func(job *types.PartnerProvisioningJob) {
					if mutation == "manager" {
						job.State.TeamManagerUserID = ""
					}
					if mutation == "status" {
						job.Status = "blocked"
					}
					if mutation == "mode" {
						job.State.KnowledgeMode = ""
					}
				})
			}
			_, err = s.CreateTeamInvitation(ctx, TeamInvitationInput{Kind: "link"})
			require.ErrorIs(t, err, ErrDenied)
			active, err = repo.GetActiveByToken(ctx, link.Token)
			require.NoError(t, err)
			require.Nil(t, active)
			invitation, err := repo.GetByID(ctx, email.ID)
			require.NoError(t, err)
			require.NotNil(t, invitation)
			require.False(t, invitation.TeamManagerAuthorized)
		})
	}
}

func TestTeamRevokeOnlyOwnCurrentTenantInvitation(t *testing.T) {
	s, ctx := teamFixture(t)
	created, err := s.CreateTeamInvitation(ctx, TeamInvitationInput{Kind: "link"})
	require.NoError(t, err)
	other := "owner"
	ownerInvitation := types.TenantInvitation{TenantID: 1, InvitedBy: &other, Role: types.TenantRoleViewer, Status: types.TenantInvitationStatusPending, Token: "owner-only-link"}
	require.NoError(t, s.DB.Create(&ownerInvitation).Error)
	require.ErrorIs(t, s.RevokeTeamInvitation(ctx, ownerInvitation.ID), ErrDenied)
	require.NoError(t, s.RevokeTeamInvitation(ctx, created.ID))
	var invitation types.TenantInvitation
	require.NoError(t, s.DB.First(&invitation, created.ID).Error)
	require.Empty(t, invitation.Token)
	require.Equal(t, types.TenantInvitationStatusRevoked, invitation.Status)
	repo := repository.NewTenantInvitationRepository(s.DB)
	legacy, err := repo.GetByID(ctx, ownerInvitation.ID)
	require.NoError(t, err)
	require.NotNil(t, legacy)
}

func TestTeamInvitationLimitAndLegacyStateSerialization(t *testing.T) {
	s, ctx := teamFixture(t)
	for i := 0; i < 20; i++ {
		_, err := s.CreateTeamInvitation(ctx, TeamInvitationInput{Kind: "link"})
		require.NoError(t, err)
	}
	_, err := s.CreateTeamInvitation(ctx, TeamInvitationInput{Kind: "link"})
	require.True(t, errors.Is(err, ErrConflict))
	serialized, err := json.Marshal(types.PartnerProvisioningState{})
	require.NoError(t, err)
	require.False(t, strings.Contains(string(serialized), "team_manager"))
}
