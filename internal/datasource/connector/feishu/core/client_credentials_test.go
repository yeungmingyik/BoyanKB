package core

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/sirupsen/logrus"
)

type credentialTestTransport func(*http.Request) (*http.Response, error)

func (f credentialTestTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	return f(r)
}

func TestGetTenantAccessTokenDoesNotLogCredentials(t *testing.T) {
	const token = "HEAD1234-dummy-tenant-token-TAIL"
	var output bytes.Buffer
	log := logrus.New()
	log.SetOutput(&output)
	ctx := context.WithValue(context.Background(), types.LoggerContextKey, logrus.NewEntry(log))
	client := &Client{
		baseURL:   "https://example.invalid",
		appID:     "dummy-app-id",
		appSecret: "dummy-app-secret",
		httpClient: &http.Client{Transport: credentialTestTransport(func(req *http.Request) (*http.Response, error) {
			if req.Method != http.MethodPost || req.URL.Path != "/open-apis/auth/v3/tenant_access_token/internal" {
				t.Fatal("unexpected token request")
			}
			var credentials map[string]string
			if err := json.NewDecoder(req.Body).Decode(&credentials); err != nil {
				t.Fatalf("decode credentials: %v", err)
			}
			if credentials["app_id"] != "dummy-app-id" || credentials["app_secret"] != "dummy-app-secret" {
				t.Fatal("token request credentials changed")
			}
			return &http.Response{
				StatusCode: http.StatusOK,
				Body:       io.NopCloser(strings.NewReader(`{"code":0,"tenant_access_token":"` + token + `","expire":7200}`)),
				Header:     make(http.Header),
			}, nil
		})},
	}
	got, err := client.GetTenantAccessToken(ctx)
	if err != nil || got != token {
		t.Fatalf("token acquisition failed: %v", err)
	}
	for _, secret := range []string{token, "HEAD1234", "TAIL", "dummy-app-id", "dummy-app-secret"} {
		if strings.Contains(output.String(), secret) {
			t.Fatalf("token log contains credential fragment %q", secret)
		}
	}
	if !strings.Contains(output.String(), "expire=7200s") {
		t.Fatal("token expiry was not logged")
	}
}
