package businessworkflow

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Tencent/WeKnora/internal/boyanskills"
	"github.com/Tencent/WeKnora/internal/types"
)

func skillCatalogFixture(t *testing.T) (*Service, context.Context) {
	t.Helper()
	s, ctx, _ := fixture(t)
	root, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("BOYAN_PRESETS_ROOT", filepath.Join(root, "presets", "boyan", "v1"))
	t.Setenv("BOYAN_SKILLS_ROOT", filepath.Join(root, "skills", "boyan"))
	if err := s.DB.AutoMigrate(&types.TenantMember{}); err != nil {
		t.Fatal(err)
	}
	member := types.TenantMember{TenantID: 1, UserID: "user", Role: types.TenantRoleViewer, Status: types.TenantMemberStatusActive}
	if err := s.DB.Create(&member).Error; err != nil {
		t.Fatal(err)
	}
	updateSkillProvision(t, s, func(job *types.PartnerProvisioningJob) {
		job.State.RestrictedSkills = make(map[string]types.PartnerRestrictedSkill)
		for _, definition := range businessSkillDefinitions {
			guide, err := boyanskills.Fixed(definition.Name)
			if err != nil {
				t.Fatal(err)
			}
			job.State.RestrictedSkills[definition.Name] = types.PartnerRestrictedSkill{Enabled: true, Version: guide.Version, SHA256: guide.SHA256, CatalogID: "catalog-" + definition.Name}
		}
	})
	return s, ctx
}

func updateSkillProvision(t *testing.T, s *Service, update func(*types.PartnerProvisioningJob)) {
	t.Helper()
	var job types.PartnerProvisioningJob
	if err := s.DB.Where("tenant_id = ?", 1).Take(&job).Error; err != nil {
		t.Fatal(err)
	}
	update(&job)
	if err := s.DB.Save(&job).Error; err != nil {
		t.Fatal(err)
	}
}

func TestBusinessSkillCatalogReadyAndAllowedDetail(t *testing.T) {
	s, ctx := skillCatalogFixture(t)
	for _, role := range []types.TenantRole{types.TenantRoleViewer, types.TenantRoleContributor, types.TenantRoleAdmin, types.TenantRoleOwner} {
		if err := s.DB.Model(&types.TenantMember{}).Where("tenant_id = ? AND user_id = ?", 1, "user").Update("role", role).Error; err != nil {
			t.Fatal(err)
		}
		catalog, err := s.SkillCatalog(ctx)
		if err != nil || !catalog.Installed || !catalog.Available || catalog.Status != "ready" || catalog.PlannerAgentID != "planner" || len(catalog.Skills) != 9 {
			t.Fatalf("ready catalog: %+v, %v", catalog, err)
		}
		for _, item := range catalog.Skills {
			if !item.Installed || !item.Available || !item.HashVerified || item.Status != "ready" || item.Version != boyanskills.Version || item.DisplayName == item.Name || item.Reason == "" || (item.Kind != "scene" && item.Kind != "format") {
				t.Fatalf("invalid item: %+v", item)
			}
			detail, err := s.SkillDetail(ctx, item.Name)
			if err != nil || digest([]byte(detail.Content)) != item.SHA256 || !strings.Contains(detail.Content, "博研文化") {
				t.Fatalf("detail validation failed: %s, %v", item.Name, err)
			}
		}
		data, err := json.Marshal(catalog)
		if err != nil || strings.Contains(string(data), "content") || strings.Contains(string(data), "catalog_id") || strings.Contains(string(data), "owner_user_id") {
			t.Fatal("catalog leaked internal content or ownership")
		}
	}
}

func TestBusinessSkillCatalogUninstalledWorkspace(t *testing.T) {
	s, ctx := skillCatalogFixture(t)
	if err := s.DB.Where("tenant_id = ?", 1).Delete(&types.PartnerProvisioningJob{}).Error; err != nil {
		t.Fatal(err)
	}
	catalog, err := s.SkillCatalog(ctx)
	if err != nil || catalog.Installed || catalog.Available || catalog.Status != "not_installed" || catalog.PlannerAgentID != "" || len(catalog.Skills) != 9 {
		t.Fatalf("uninstalled catalog: %+v, %v", catalog, err)
	}
	for _, item := range catalog.Skills {
		if item.Installed || item.Available || item.HashVerified || item.Status != "not_installed" {
			t.Fatalf("uninstalled skill reported ready: %+v", item)
		}
	}
	if _, err := s.SkillDetail(ctx, "boyan-courseware"); !errors.Is(err, ErrDenied) {
		t.Fatalf("uninstalled content was readable: %v", err)
	}
}

func TestBusinessSkillCatalogRejectsNonWebAndMismatchedIdentity(t *testing.T) {
	s, ctx := skillCatalogFixture(t)
	for _, kind := range []string{types.PrincipalAPITenant, types.PrincipalAPIPlatform, types.PrincipalAPIExternalUser, types.PrincipalEmbedSession, types.PrincipalIMUser} {
		t.Run(kind, func(t *testing.T) {
			other := types.WithPrincipal(ctx, types.Principal{Type: kind, ID: "user"})
			if _, err := s.SkillCatalog(other); !errors.Is(err, ErrDenied) {
				t.Fatalf("machine catalog accepted: %v", err)
			}
			if _, err := s.SkillDetail(other, "boyan-courseware"); !errors.Is(err, ErrDenied) {
				t.Fatalf("machine detail accepted: %v", err)
			}
		})
	}
	other := types.WithPrincipal(ctx, types.Principal{Type: types.PrincipalWebUser, ID: "other"})
	if _, err := s.SkillCatalog(other); !errors.Is(err, ErrDenied) {
		t.Fatalf("mismatched web identity accepted: %v", err)
	}
	if _, err := s.SkillCatalog(context.Background()); !errors.Is(err, ErrDenied) {
		t.Fatalf("anonymous caller accepted: %v", err)
	}
}

func TestBusinessSkillCatalogRequiresActiveLocalMembership(t *testing.T) {
	for _, state := range []string{"missing", "suspended", "invited", "deleted", "invalid_role"} {
		t.Run(state, func(t *testing.T) {
			s, ctx := skillCatalogFixture(t)
			query := s.DB.Model(&types.TenantMember{}).Where("tenant_id = ? AND user_id = ?", 1, "user")
			var err error
			switch state {
			case "missing":
				err = query.Unscoped().Delete(&types.TenantMember{}).Error
			case "deleted":
				err = query.Delete(&types.TenantMember{}).Error
			case "invalid_role":
				err = query.Update("role", "unknown").Error
			default:
				err = query.Update("status", state).Error
			}
			if err != nil {
				t.Fatal(err)
			}
			if _, err := s.SkillCatalog(ctx); !errors.Is(err, ErrDenied) {
				t.Fatalf("invalid membership accepted: %v", err)
			}
		})
	}
	s, ctx := skillCatalogFixture(t)
	other := types.WithCaller(ctx, types.Caller{TenantID: 2, UserID: "user", Role: types.TenantRoleOwner})
	if _, err := s.SkillCatalog(other); !errors.Is(err, ErrDenied) {
		t.Fatalf("cross-tenant membership bypass: %v", err)
	}
	if err := s.DB.Create(&types.TenantMember{TenantID: 2, UserID: "user", Role: types.TenantRoleViewer, Status: types.TenantMemberStatusActive}).Error; err != nil {
		t.Fatal(err)
	}
	catalog, err := s.SkillCatalog(other)
	if err != nil || catalog.Available || catalog.PlannerAgentID != "" || catalog.Status != "not_installed" {
		t.Fatalf("another tenant's installation leaked: %+v, %v", catalog, err)
	}
}

func TestBusinessSkillCatalogUnavailableInstallations(t *testing.T) {
	for _, state := range []string{"pending", "missing", "disabled", "wrong_hash", "wrong_version", "missing_catalog", "missing_planner", "foreign_planner", "foreign_owner", "builtin_planner"} {
		t.Run(state, func(t *testing.T) {
			s, ctx := skillCatalogFixture(t)
			updateSkillProvision(t, s, func(job *types.PartnerProvisioningJob) {
				item := job.State.RestrictedSkills["boyan-courseware"]
				switch state {
				case "pending":
					job.Status = "installing"
				case "missing":
					delete(job.State.RestrictedSkills, "boyan-courseware")
					return
				case "disabled":
					item.Enabled = false
				case "wrong_hash":
					item.SHA256 = strings.Repeat("0", 64)
				case "wrong_version":
					item.Version = "boyan/v2"
				case "missing_catalog":
					item.CatalogID = ""
				case "missing_planner":
					job.State.AgentIDs["planner"] = "missing"
				case "foreign_planner":
					if err := s.DB.Model(&types.CustomAgent{}).Where("id = ?", "planner").Update("tenant_id", 2).Error; err != nil {
						t.Fatal(err)
					}
				case "foreign_owner":
					job.OwnerUserID = "other"
				case "builtin_planner":
					if err := s.DB.Model(&types.CustomAgent{}).Where("id = ?", "planner").Update("is_builtin", true).Error; err != nil {
						t.Fatal(err)
					}
				}
				job.State.RestrictedSkills["boyan-courseware"] = item
			})
			catalog, err := s.SkillCatalog(ctx)
			if err != nil || catalog.Available || catalog.Status == "ready" || catalog.Skills[0].Available {
				t.Fatalf("invalid installation reported available: %+v, %v", catalog, err)
			}
			if strings.Contains(state, "planner") || state == "foreign_owner" {
				if catalog.PlannerAgentID != "" {
					t.Fatal("invalid planner binding leaked")
				}
			}
			if _, err := s.SkillDetail(ctx, "boyan-courseware"); !errors.Is(err, ErrDenied) {
				t.Fatalf("invalid installation was readable: %v", err)
			}
		})
	}
}

func TestBusinessSkillCatalogRejectsTamperedFilesAndArbitraryNames(t *testing.T) {
	s, ctx := skillCatalogFixture(t)
	for _, name := range []string{"", "../courseware", "boyan-courseware/../../.env", "third-party", "BOYAN-COURSEWARE"} {
		if _, err := s.SkillDetail(ctx, name); !errors.Is(err, ErrDenied) {
			t.Fatalf("unexpected name accepted: %q, %v", name, err)
		}
	}
	root := t.TempDir()
	if err := os.Mkdir(filepath.Join(root, "courseware"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "courseware", "SKILL.md"), []byte("untrusted modified content"), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("BOYAN_SKILLS_ROOT", root)
	catalog, err := s.SkillCatalog(ctx)
	if err != nil || !catalog.Installed || catalog.Available || catalog.Skills[0].HashVerified || catalog.Skills[0].SHA256 != "" {
		t.Fatalf("tampered content accepted: %+v, %v", catalog, err)
	}
	if _, err := s.SkillDetail(ctx, "boyan-courseware"); !errors.Is(err, ErrDenied) {
		t.Fatalf("tampered content was readable: %v", err)
	}
}

func TestBusinessSkillCatalogStorageFailureIsClosed(t *testing.T) {
	s, ctx := skillCatalogFixture(t)
	if err := s.DB.Migrator().DropTable(&types.PartnerProvisioningJob{}); err != nil {
		t.Fatal(err)
	}
	if _, err := s.SkillCatalog(ctx); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("database failure appeared uninstalled: %v", err)
	}
}
