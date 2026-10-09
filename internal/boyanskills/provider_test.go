package boyanskills

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/DATA-DOG/go-sqlmock"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

func fixedRoots(t *testing.T) {
	t.Helper()
	root, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("BOYAN_PRESETS_ROOT", filepath.Join(root, "presets", "boyan", "v1"))
	t.Setenv("BOYAN_SKILLS_ROOT", filepath.Join(root, "skills", "boyan"))
}

func TestFixedPackages(t *testing.T) {
	fixedRoots(t)
	for name := range names {
		t.Run(name, func(t *testing.T) {
			guide, err := Fixed(name)
			if err != nil {
				t.Fatal(err)
			}
			if guide.Name != name || guide.Version != Version || len(guide.Description) == 0 || len(guide.Instructions) == 0 || !strings.Contains(guide.Content, "博研文化") {
				t.Fatal("incomplete guide")
			}
			if err := ValidateFixedSkill(name, guide.Version, guide.SHA256); err != nil {
				t.Fatal(err)
			}
			if err := ValidateFixedSkill(name, "boyan/v2", guide.SHA256); !errors.Is(err, ErrDenied) {
				t.Fatalf("unexpected version result: %v", err)
			}
		})
	}
}

func TestFixedRejectsUnknownAndTamperedPackages(t *testing.T) {
	fixedRoots(t)
	for _, name := range []string{"", "../courseware", "boyan-courseware/../../.env", "third-party"} {
		if _, err := Fixed(name); !errors.Is(err, ErrDenied) {
			t.Fatalf("accepted %q: %v", name, err)
		}
	}
	root := t.TempDir()
	if err := os.Mkdir(filepath.Join(root, "courseware"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "courseware", "SKILL.md"), []byte("modified instructions"), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("BOYAN_SKILLS_ROOT", root)
	if _, err := Fixed("boyan-courseware"); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("accepted tampered package: %v", err)
	}
}

func TestFixedManifestRejectsDuplicateAndMissingAllowlist(t *testing.T) {
	fixedRoots(t)
	data, err := os.ReadFile(filepath.Join(presetRoot(), "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if err := json.Unmarshal(data, &m); err != nil {
		t.Fatal(err)
	}
	items := m["skills"].([]any)
	items[len(items)-1] = items[0]
	data, err = json.Marshal(m)
	if err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "manifest.json"), data, 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("BOYAN_PRESETS_ROOT", root)
	if _, err := Fixed("boyan-courseware"); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("accepted duplicate allowlist: %v", err)
	}
}

func TestFixedReadRejectsSymlinkEscape(t *testing.T) {
	root := t.TempDir()
	outside := filepath.Join(t.TempDir(), "outside.md")
	if err := os.WriteFile(outside, []byte("private"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(root, "link.md")); err != nil {
		t.Skip("symlink unavailable")
	}
	if _, err := readFixed(root, "link.md"); !errors.Is(err, ErrDenied) {
		t.Fatalf("symlink escaped root: %v", err)
	}
}

func TestReadUsesTenantInstalledRevision(t *testing.T) {
	fixedRoots(t)
	guide, err := Fixed("boyan-courseware")
	if err != nil {
		t.Fatal(err)
	}
	valid := installation{Version: Version, SHA256: guide.SHA256, CatalogID: "catalog-owned-by-partner", Enabled: true}
	cases := []struct {
		name      string
		status    string
		installed installation
		missing   bool
		malformed bool
		err       error
	}{
		{name: "installed", status: "ready", installed: valid},
		{name: "disabled", status: "ready", installed: installation{Version: Version, SHA256: guide.SHA256, CatalogID: "catalog-owned-by-partner"}, err: ErrDenied},
		{name: "wrong-digest", status: "ready", installed: installation{Version: Version, SHA256: strings.Repeat("0", 64), CatalogID: "catalog-owned-by-partner", Enabled: true}, err: ErrDenied},
		{name: "wrong-version", status: "ready", installed: installation{Version: "boyan/v0", SHA256: guide.SHA256, CatalogID: "catalog-owned-by-partner", Enabled: true}, err: ErrDenied},
		{name: "missing-catalog", status: "ready", installed: installation{Version: Version, SHA256: guide.SHA256, Enabled: true}, err: ErrDenied},
		{name: "incomplete-provisioning", status: "provisioning", installed: valid, err: ErrDenied},
		{name: "other-tenant-has-no-install", status: "ready", missing: true, err: ErrDenied},
		{name: "malformed-state", status: "ready", installed: valid, malformed: true, err: ErrDenied},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			sqlDB, mock, err := sqlmock.New()
			if err != nil {
				t.Fatal(err)
			}
			defer sqlDB.Close()
			db, err := gorm.Open(postgres.New(postgres.Config{Conn: sqlDB}), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
			if err != nil {
				t.Fatal(err)
			}
			entries := map[string]installation{}
			if !tc.missing {
				entries[guide.Name] = tc.installed
			}
			state, err := json.Marshal(map[string]any{"restricted_skills": entries})
			if err != nil {
				t.Fatal(err)
			}
			if tc.malformed {
				state = []byte("invalid")
			}
			mock.ExpectQuery(`SELECT status, state FROM "partner_provisioning_jobs" WHERE tenant_id = \$1 LIMIT \$2`).WithArgs(uint64(222), 1).WillReturnRows(sqlmock.NewRows([]string{"status", "state"}).AddRow(tc.status, state))
			body, err := Read(context.Background(), db, 222, guide.Name)
			if !errors.Is(err, tc.err) {
				t.Fatalf("got %v, expected %v", err, tc.err)
			}
			if tc.err == nil && body != guide.Content {
				t.Fatal("did not return fixed package")
			}
			if tc.err != nil && body != "" {
				t.Fatal("returned denied content")
			}
			if err := mock.ExpectationsWereMet(); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestReadFailsClosedOnDatabaseError(t *testing.T) {
	sqlDB, mock, err := sqlmock.New()
	if err != nil {
		t.Fatal(err)
	}
	defer sqlDB.Close()
	db, err := gorm.Open(postgres.New(postgres.Config{Conn: sqlDB}), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	if err != nil {
		t.Fatal(err)
	}
	mock.ExpectQuery(`SELECT status, state`).WithArgs(uint64(333), 1).WillReturnError(errors.New("offline"))
	if content, err := Read(context.Background(), db, 333, "boyan-courseware"); !errors.Is(err, ErrUnavailable) || content != "" {
		t.Fatalf("database failure allowed content: %v", err)
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Fatal(err)
	}
	if _, err := Read(context.Background(), nil, 0, "boyan-courseware"); !errors.Is(err, ErrDenied) {
		t.Fatal("nil database allowed")
	}
}

func TestAgentPromptsUseFixedRoles(t *testing.T) {
	fixedRoots(t)
	for _, name := range []string{"mentor", "planner", "reviewer"} {
		prompt, err := ReadAgentPrompt(name)
		if err != nil || !strings.Contains(prompt, "博研文化共同规则") || !strings.Contains(prompt, "资料中没有这部分信息") {
			t.Fatalf("invalid prompt %s: %v", name, err)
		}
	}
	for _, name := range []string{"../reviewer", "unknown", "../../.env"} {
		if _, err := ReadAgentPrompt(name); !errors.Is(err, ErrDenied) {
			t.Fatalf("accepted arbitrary prompt %s", name)
		}
	}
}
