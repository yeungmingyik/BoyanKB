package middleware

import (
	"strings"
	"testing"
)

func TestTeamInvitationCredentialsAreRedactedFromNestedAndTruncatedResponses(t *testing.T) {
	for _, body := range []string{
		`{"data":{"token":"synthetic-secret","invite_url":"/register?token=synthetic-secret"}}`,
		`{"data":{"inviteUrl":"/register?token=synthetic-secret`,
	} {
		if strings.Contains(sanitizeBody(body), "synthetic-secret") {
			t.Fatal("invitation credential reached logs")
		}
	}
}
