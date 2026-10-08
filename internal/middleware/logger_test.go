package middleware

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/gin-gonic/gin"
	"github.com/sirupsen/logrus"
)

func TestSanitizeBody(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want string
	}{
		{
			name: "browser device credentials",
			in: `{"pairing_link":"wss://example.com/#secret",` +
				`"next_token":"new-secret","deviceToken":"device-secret"}`,
			want: `{"pairing_link":"***","next_token":"***","deviceToken":"***"}`,
		},
		{
			name: "camelCase apiKey",
			in:   `{"modelName":"gpt-5.2","apiKey":"sk-secret-123","provider":"azure_openai"}`,
			want: `{"modelName":"gpt-5.2","apiKey":"***","provider":"azure_openai"}`,
		},
		{
			name: "snake_case api_key",
			in:   `{"api_key":"sk-secret-123"}`,
			want: `{"api_key":"***"}`,
		},
		{
			name: "PascalCase APIKey",
			in:   `{"APIKey":"sk-secret-123"}`,
			want: `{"APIKey":"***"}`,
		},
		{
			name: "secretKey camelCase",
			in:   `{"secretKey":"abc","accessKeyId":"id"}`,
			want: `{"secretKey":"***","accessKeyId":"id"}`,
		},
		{
			name: "refreshToken / accessToken camelCase",
			in:   `{"refreshToken":"rt","accessToken":"at"}`,
			want: `{"refreshToken":"***","accessToken":"***"}`,
		},
		{
			name: "password and token preserved as masked",
			in:   `{"password":"p","token":"t"}`,
			want: `{"password":"***","token":"***"}`,
		},
		{
			name: "sandbox terminal handshake ticket in JSON body",
			in:   `{"success":true,"data":{"ticket":"eyJhbGciOiJIUzI1NiJ9.payload.signature","expires_in":120}}`,
			want: `{"success":true,"data":{"ticket":"***","expires_in":120}}`,
		},
		{
			name: "snake_case new_password and old_password",
			in:   `{"email":"alice@example.com","new_password":"FreshPass9","old_password":"OldPass9"}`,
			want: `{"email":"alice@example.com","new_password":"***","old_password":"***"}`,
		},
		{
			name: "extra whitespace around colon",
			in:   `{"apiKey"  :   "leak"}`,
			want: `{"apiKey":"***"}`,
		},
		{
			name: "non sensitive fields untouched",
			in:   `{"baseUrl":"https://example.com","modelName":"gpt"}`,
			want: `{"baseUrl":"https://example.com","modelName":"gpt"}`,
		},
		{
			name: "OAuth authorization response fields",
			in:   `{"authorization_url":"https://idp.example/authorize?state=secret","authorization_attempt":"secret-state"}`,
			want: `{"authorization_url":"***","authorization_attempt":"***"}`,
		},
		{
			name: "nested datasource credentials",
			in:   `{"config":{"credentials":{"app_id":"dummy-app","app_secret":"dummy-secret"}},"items":[{"feishu_app_secret":"dummy-feishu-secret","AppSecret":"dummy-camel-secret","appId":"dummy-camel-app"}]}`,
			want: `{"config":{"credentials":{"app_id":"***","app_secret":"***"}},"items":[{"feishu_app_secret":"***","AppSecret":"***","appId":"***"}]}`,
		},
		{
			name: "escaped secret value",
			in:   `{"app_secret":"first\"second\\third","name":"visible"}`,
			want: `{"app_secret":"***","name":"visible"}`,
		},
		{
			name: "truncated secret value",
			in:   `{"config":{"credentials":{"app_secret":"truncated-secret`,
			want: `{"config":{"credentials":{"app_secret":"***"`,
		},
		{
			name: "nested authorization and tenant token",
			in:   `{"data":{"authorization":"Bearer dummy-auth","tenant_access_token":"dummy-tenant-token","token":"dummy-token"}}`,
			want: `{"data":{"authorization":"***","tenant_access_token":"***","token":"***"}}`,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := sanitizeBody(tc.in)
			if got != tc.want {
				t.Errorf("sanitizeBody(%q)\n got: %s\nwant: %s", tc.in, got, tc.want)
			}
		})
	}
}

func TestLoggerRedactsDataSourceCredentials(t *testing.T) {
	const body = `{"config":{"credentials":{"app_id":"dummy-app-id","app_secret":"dummy-app-secret"}},"data":{"authorization":"Bearer dummy-auth-token","tenant_access_token":"dummy-tenant-token"}}`
	cases := []struct {
		method string
		path   string
		status int
	}{
		{http.MethodPost, "/api/v1/datasource", http.StatusCreated},
		{http.MethodPost, "/api/v1/datasource/validate-credentials", http.StatusBadRequest},
		{http.MethodPut, "/api/v1/datasource/test/credentials", http.StatusInternalServerError},
	}
	for _, tc := range cases {
		t.Run(tc.path, func(t *testing.T) {
			var output bytes.Buffer
			log := logrus.New()
			log.SetOutput(&output)
			log.SetFormatter(&logrus.JSONFormatter{})
			r := gin.New()
			r.ContextWithFallback = true
			r.Use(Logger())
			r.Handle(tc.method, tc.path, func(c *gin.Context) {
				got, err := io.ReadAll(c.Request.Body)
				if err != nil || string(got) != body {
					t.Fatal("logging changed the request body")
				}
				c.Data(tc.status, "application/json", got)
			})
			req := httptest.NewRequest(tc.method, tc.path, strings.NewReader(body))
			req.Header.Set("Content-Type", "application/json")
			req.Header.Set("Authorization", "Bearer dummy-header-token")
			req = req.WithContext(context.WithValue(req.Context(), types.LoggerContextKey, logrus.NewEntry(log)))
			response := httptest.NewRecorder()
			r.ServeHTTP(response, req)
			if response.Code != tc.status || response.Body.String() != body {
				t.Fatal("logging changed the response")
			}
			for _, secret := range []string{"dummy-app-id", "dummy-app-secret", "dummy-auth-token", "dummy-tenant-token", "dummy-header-token"} {
				if strings.Contains(output.String(), secret) {
					t.Fatalf("access log contains credential %q", secret)
				}
			}
			var entry map[string]interface{}
			if err := json.Unmarshal(output.Bytes(), &entry); err != nil {
				t.Fatalf("decode access log: %v", err)
			}
			for _, field := range []string{"request_body", "response_body"} {
				if entry[field] != sanitizeBody(body) {
					t.Errorf("%s was not redacted", field)
				}
			}
		})
	}
}

func TestSanitizeQuery(t *testing.T) {
	got := sanitizeQuery("code=secret-code&state=secret-state&next=%2Fsettings&state=second")
	want := "code=%2A%2A%2A&next=%2Fsettings&state=%2A%2A%2A"
	if got != want {
		t.Fatalf("sanitizeQuery() = %q, want %q", got, want)
	}
}

// The sandbox terminal presents its handshake credential as a query parameter
// because a browser WebSocket upgrade cannot carry Authorization. Holding that
// value for its TTL is enough to open a shell in the session's sandbox, so the
// redaction is a security boundary, not cosmetics.
func TestSanitizeQueryRedactsTerminalTicket(t *testing.T) {
	got := sanitizeQuery("ticket=eyJhbGciOiJIUzI1NiJ9.payload.signature&provision=1&cols=120")
	want := "cols=120&provision=1&ticket=%2A%2A%2A"
	if got != want {
		t.Fatalf("sanitizeQuery() = %q, want %q", got, want)
	}
	if strings.Contains(got, "signature") {
		t.Fatalf("sanitizeQuery() leaked the ticket: %q", got)
	}
}
