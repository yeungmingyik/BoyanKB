package handler

import (
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Tencent/WeKnora/internal/businessworkflow"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestBusinessTeamRejectsRoleTenantSelectorsAndAnonymousCalls(t *testing.T) {
	h := NewBusinessWorkflowHandler(&businessworkflow.Service{})
	r := gin.New()
	r.GET("/team", h.Team)
	r.POST("/team/invitations", h.CreateTeamInvitation)
	r.DELETE("/team/invitations/:invitation_id", h.RevokeTeamInvitation)
	for _, test := range []struct {
		method, path, body string
		code               int
	}{
		{"GET", "/team?tenant_id=99", "", 400},
		{"GET", "/team", "", 403},
		{"POST", "/team/invitations", `{"kind":"link","role":"owner"}`, 400},
		{"POST", "/team/invitations", `{"kind":"link","tenant_id":99}`, 400},
		{"POST", "/team/invitations", `{"kind":"link","invited_by":"other"}`, 400},
		{"POST", "/team/invitations", `{"kind":"link"} {}`, 400},
		{"POST", "/team/invitations", `{"kind":"link"}`, 403},
		{"DELETE", "/team/invitations/0", "", 400},
		{"DELETE", "/team/invitations/1", "", 403},
	} {
		response := httptest.NewRecorder()
		r.ServeHTTP(response, httptest.NewRequest(test.method, test.path, strings.NewReader(test.body)))
		require.Equal(t, test.code, response.Code, test.path+test.body)
		require.Equal(t, "no-store", response.Header().Get("Cache-Control"))
	}
}

func TestOwnerListCannotRecoverDelegatedTeamLink(t *testing.T) {
	h := &TenantInvitationHandler{}
	invitation := &types.TenantInvitation{TeamManagerUserID: "manager", Token: "synthetic-secret", Role: types.TenantRoleViewer, Status: types.TenantInvitationStatusPending}
	result := h.projectInvitationWithLink(invitation, nil, nil)
	require.Empty(t, result.InviteURL)
}
