package service

import (
	"context"
	"testing"
	"time"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/stretchr/testify/require"
)

func TestDelegatedEmailInvitationRequiresLiveAuthorizationButOwnerCanRevoke(t *testing.T) {
	for _, authorized := range []bool{false, true} {
		svc, repo, members := newInvitationSvc()
		manager := "manager"
		invitation := &types.TenantInvitation{TenantID: 1, InviteeUserID: "employee", Role: types.TenantRoleViewer, Status: types.TenantInvitationStatusPending, ExpiresAt: time.Now().Add(time.Hour), InvitedBy: &manager, TeamManagerUserID: manager, TeamManagerAuthorized: authorized}
		require.NoError(t, repo.Create(context.Background(), invitation))
		member, err := svc.Accept(context.Background(), invitation.ID, "employee")
		if authorized {
			require.NoError(t, err)
			require.Equal(t, types.TenantRoleViewer, member.Role)
		} else {
			require.ErrorIs(t, err, ErrInvitationForbidden)
			member, err = members.GetMembership(context.Background(), "employee", 1)
			require.NoError(t, err)
			require.Nil(t, member)
			require.NoError(t, svc.Revoke(context.Background(), invitation.ID))
			row, err := repo.GetByID(context.Background(), invitation.ID)
			require.NoError(t, err)
			require.Equal(t, types.TenantInvitationStatusRevoked, row.Status)
		}
	}
}
