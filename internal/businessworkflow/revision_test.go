package businessworkflow

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"testing"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
)

func TestRevisionRestoresDraftWithoutPrivatePayloadOrApprovalReuse(t *testing.T) {
	s, ctx, p := fixture(t)
	p.Budget = &Budget{Items: []BudgetItem{{Label: "资料", Quantity: 2, UnitPrice: 10}}}
	p.Document.Sections[0].Notes = "保留的讲者备注"
	job := confirmed(t, s, ctx, p)
	if err := s.DB.Model(job).Update("artifact", []byte("private-artifact-bytes")).Error; err != nil {
		t.Fatal(err)
	}
	result, err := s.Revision(ctx, "session", job.ID)
	if err != nil {
		t.Fatal(err)
	}
	revision := result["revision"].(Proposal)
	if !reflect.DeepEqual(revision, p) || result["state"] != Approved {
		t.Fatal("draft or original state changed")
	}
	data, _ := json.Marshal(result)
	for _, key := range []string{"\"artifact\"", "\"payload\"", "\"user_id\"", "\"tenant_id\"", "private-artifact-bytes", "\"skill_hashes\""} {
		if strings.Contains(string(data), key) {
			t.Fatal("private payload exposed")
		}
	}
	sources := result["verified_sources"].([]RevisionSource)
	if len(sources) != 1 || sources[0].ChunkID != "chunk" || sources[0].Title != "总部课程" {
		t.Fatal("verified source missing")
	}
	revision.Document.Sections[0].Body += "补充修订。"
	newJob := proposed(t, s, ctx, revision)
	if newJob.State != AwaitingConfirmation || newJob.ConfirmedAt != nil || newJob.OutlineHash == job.OutlineHash {
		t.Fatal("revision inherited approval")
	}
	if _, err := s.Generate(ctx, "session", newJob.ID, newJob.OutlineHash); !errors.Is(err, ErrConflict) {
		t.Fatal("unconfirmed revision generated")
	}
}

func TestRevisionRejectsBadOrUnboundPayload(t *testing.T) {
	for _, mode := range []string{"invalid_json", "trailing_json", "unknown_field", "changed_hash", "unverified_source", "duplicate_source"} {
		t.Run(mode, func(t *testing.T) {
			s, ctx, p := fixture(t)
			job := proposed(t, s, ctx, p)
			data := append([]byte(nil), job.Payload...)
			switch mode {
			case "invalid_json":
				data = []byte("{")
			case "trailing_json":
				data = append(data, []byte(" {}")...)
			case "unknown_field":
				data = []byte(strings.Replace(string(data), "{", `{"arbitrary":true,`, 1))
			case "changed_hash":
				data = []byte(strings.Replace(string(data), "课程问答", "更改问答", 1))
			default:
				var payload Payload
				_ = json.Unmarshal(data, &payload)
				if mode == "unverified_source" {
					payload.Sources[0].ChunkID = "foreign-chunk"
				} else {
					payload.Sources = append(payload.Sources, payload.Sources[0])
					payload.VerifiedSources = append(payload.VerifiedSources, payload.VerifiedSources[0])
				}
				data, _ = json.Marshal(payload)
			}
			updates := map[string]interface{}{"payload": data}
			if mode != "changed_hash" {
				updates["outline_hash"] = digest(data)
			}
			if err := s.DB.Model(job).Updates(updates).Error; err != nil {
				t.Fatal(err)
			}
			if result, err := s.Revision(ctx, "session", job.ID); err == nil || result != nil {
				t.Fatal("invalid revision exposed")
			}
		})
	}
}

func TestRevisionRequiresCurrentSkillAndSourceAccess(t *testing.T) {
	for _, mode := range []string{"skill_revoked", "skill_changed", "content_changed", "disabled", "scope_revoked"} {
		t.Run(mode, func(t *testing.T) {
			s, ctx, p := fixture(t)
			job := proposed(t, s, ctx, p)
			switch mode {
			case "skill_revoked":
				s.Skills = func(context.Context, string, string) (map[string]string, error) { return nil, ErrDenied }
			case "skill_changed":
				s.Skills = func(context.Context, string, string) (map[string]string, error) {
					return map[string]string{"scene": "changed"}, nil
				}
			case "content_changed":
				s.DB.Exec("UPDATE chunks SET content = 'changed'")
			case "disabled":
				s.DB.Exec("UPDATE chunks SET is_enabled = false")
			case "scope_revoked":
				s.DB.Model(&types.CustomAgent{}).Where("id = ?", "planner").Update("config", types.CustomAgentConfig{KBSelectionMode: "none"})
			}
			if result, err := s.Revision(ctx, "session", job.ID); err == nil || result != nil {
				t.Fatal("unavailable draft source exposed")
			}
		})
	}
}

func TestRevisionRequiresSameActorTenantSession(t *testing.T) {
	s, ctx, p := fixture(t)
	job := proposed(t, s, ctx, p)
	for _, caller := range []types.Caller{{TenantID: 2, UserID: "user"}, {TenantID: 1, UserID: "other"}} {
		if _, err := s.Revision(types.WithCaller(ctx, caller), "session", job.ID); !errors.Is(err, ErrDenied) {
			t.Fatal("foreign actor read draft")
		}
	}
	if _, err := s.Revision(ctx, "second-session", job.ID); !errors.Is(err, ErrDenied) {
		t.Fatal("foreign session read draft")
	}
	if _, err := s.Revision(types.WithPrincipal(ctx, types.Principal{Type: types.PrincipalAPITenant, ID: "key"}), "session", job.ID); !errors.Is(err, ErrDenied) {
		t.Fatal("API principal read draft")
	}
}

type revisionShares struct {
	interfaces.KBShareService
	allowed bool
}

func (s *revisionShares) CheckTenantKBPermission(context.Context, string, uint64, types.TenantRole) (types.OrgMemberRole, bool, error) {
	return types.OrgRoleViewer, s.allowed, nil
}

func TestRevisionRechecksHeadquartersShareAndCurrentSourceTitle(t *testing.T) {
	s, ctx, p := fixture(t)
	for _, table := range []string{"knowledge_bases", "knowledges", "chunks"} {
		if err := s.DB.Table(table).Where("tenant_id = ?", 1).Update("tenant_id", 2).Error; err != nil {
			t.Fatal(err)
		}
	}
	if err := s.DB.Model(&types.PartnerProvisioningJob{}).Where("tenant_id = ?", 1).Updates(map[string]interface{}{"hq_knowledge_base_ids": types.StringArray{"kb"}, "state": types.PartnerProvisioningState{AgentIDs: map[string]string{"planner": "planner", "reviewer": "reviewer"}, KnowledgeBaseID: "private-kb"}}).Error; err != nil {
		t.Fatal(err)
	}
	shares := &revisionShares{allowed: true}
	s.Shares = shares
	job := proposed(t, s, ctx, p)
	if err := s.DB.Exec("UPDATE knowledges SET title = '服务端当前标题'").Error; err != nil {
		t.Fatal(err)
	}
	result, err := s.Revision(ctx, "session", job.ID)
	if err != nil || result["verified_sources"].([]RevisionSource)[0].Title != "服务端当前标题" {
		t.Fatal("current verified title unavailable")
	}
	shares.allowed = false
	if result, err := s.Revision(ctx, "session", job.ID); !errors.Is(err, ErrDenied) || result != nil {
		t.Fatal("revoked headquarters share exposed")
	}
}
