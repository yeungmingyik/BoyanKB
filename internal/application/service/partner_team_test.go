package service

import (
	"encoding/json"
	"testing"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestPartnerTeamManagerRequiresActiveViewerInSharedWorkspace(t *testing.T) {
	for _, mutation := range []string{"valid", "owner", "contributor", "foreign", "suspended", "inactive", "legacy"} {
		t.Run(mutation, func(t *testing.T) {
			_, db, _, job := readySharedEnterprise(t, false)
			user := types.User{ID: uuid.NewString(), Username: "manager", Email: "manager@example.test", PasswordHash: "test", IsActive: true}
			require.NoError(t, db.Create(&user).Error)
			member := types.TenantMember{TenantID: job.TenantID, UserID: user.ID, Role: types.TenantRoleViewer, Status: types.TenantMemberStatusActive}
			switch mutation {
			case "owner":
				member.Role = types.TenantRoleOwner
			case "contributor":
				member.Role = types.TenantRoleContributor
			case "foreign":
				member.TenantID = job.HQTenantID
			case "suspended":
				member.Status = types.TenantMemberStatusSuspended
			case "inactive":
				require.NoError(t, db.Model(&types.User{}).Where("id = ?", user.ID).Update("is_active", false).Error)
			case "legacy":
				job.State.KnowledgeMode = ""
			}
			require.NoError(t, db.Create(&member).Error)
			state := job.State
			state.TeamManagerUserID = user.ID
			err := validatePartnerState(db, job, state, false)
			if mutation == "valid" {
				require.NoError(t, err)
			} else {
				require.ErrorIs(t, err, ErrPartnerInvalid)
			}
		})
	}
}

func TestPartnerTeamManagerCanBeClearedByAuthorizedJobUpdate(t *testing.T) {
	svc, db, ctx, job := readySharedEnterprise(t, false)
	user := types.User{ID: uuid.NewString(), Username: "manager", Email: "manager@example.test", PasswordHash: "test", IsActive: true}
	require.NoError(t, db.Create(&user).Error)
	require.NoError(t, db.Create(&types.TenantMember{TenantID: job.TenantID, UserID: user.ID, Role: types.TenantRoleViewer, Status: types.TenantMemberStatusActive}).Error)
	state := job.State
	state.RestrictedSkills = nil
	state.TeamManagerUserID = user.ID
	updated, err := svc.Update(ctx, job.PartnerKey, PartnerProvisioningUpdate{LeaseHolder: job.LeaseHolder, Revision: job.Revision, State: state, Status: "ready"})
	require.NoError(t, err)
	require.Equal(t, user.ID, updated.State.TeamManagerUserID)
	state = updated.State
	state.RestrictedSkills = nil
	state.TeamManagerUserID = ""
	cleared, err := svc.Update(ctx, job.PartnerKey, PartnerProvisioningUpdate{LeaseHolder: updated.LeaseHolder, Revision: updated.Revision, State: state, Status: "ready"})
	require.NoError(t, err)
	require.Empty(t, cleared.State.TeamManagerUserID)
	require.Equal(t, job.RequestHash, cleared.RequestHash)
	encoded, err := json.Marshal(types.PartnerProvisioningState{})
	require.NoError(t, err)
	require.JSONEq(t, "{}", string(encoded))
}
