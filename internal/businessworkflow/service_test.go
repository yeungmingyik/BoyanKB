package businessworkflow

import (
	"context"
	"encoding/json"
	"errors"
	"testing"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/google/uuid"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

func fixture(t *testing.T) (*Service, context.Context, Proposal) {
	t.Helper()
	db, err := gorm.Open(sqlite.Open("file:"+uuid.NewString()+"?mode=memory&cache=shared"), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	if err != nil {
		t.Fatal(err)
	}
	sqlDB, err := db.DB()
	if err != nil {
		t.Fatal(err)
	}
	sqlDB.SetMaxOpenConns(1)
	t.Cleanup(func() { sqlDB.Close() })
	if err := db.AutoMigrate(&Job{}, &types.PartnerProvisioningJob{}, &types.CustomAgent{}); err != nil {
		t.Fatal(err)
	}
	statements := []string{
		`CREATE TABLE sessions (id TEXT PRIMARY KEY,tenant_id INTEGER,user_id TEXT,deleted_at DATETIME)`,
		`CREATE TABLE chunks (id TEXT PRIMARY KEY,tenant_id INTEGER,knowledge_id TEXT,knowledge_base_id TEXT,is_enabled BOOLEAN,content TEXT,deleted_at DATETIME)`,
		`CREATE TABLE knowledges (id TEXT PRIMARY KEY,tenant_id INTEGER,knowledge_base_id TEXT,title TEXT,deleted_at DATETIME)`,
		`CREATE TABLE knowledge_bases (id TEXT PRIMARY KEY,tenant_id INTEGER,deleted_at DATETIME)`,
		`INSERT INTO sessions(id,tenant_id,user_id) VALUES ('session',1,'user'),('second-session',1,'user')`,
		`INSERT INTO knowledge_bases(id,tenant_id) VALUES ('kb',1)`,
		`INSERT INTO knowledges(id,tenant_id,knowledge_base_id,title) VALUES ('knowledge',1,'kb','总部课程')`,
		`INSERT INTO chunks(id,tenant_id,knowledge_id,knowledge_base_id,is_enabled,content) VALUES ('chunk',1,'knowledge','kb',true,'博研文化开展科创课程。')`,
	}
	for _, statement := range statements {
		if db.Exec(statement).Error != nil {
			t.Fatalf("fixture setup failed: %s", statement)
		}
	}
	planner := types.CustomAgent{ID: "planner", TenantID: 1, CreatedBy: "owner", Name: "落地方案官", Config: types.CustomAgentConfig{KBSelectionMode: "selected", KnowledgeBases: []string{"kb"}}}
	if db.Create(&planner).Error != nil {
		t.Fatal("planner setup failed")
	}
	provision := types.PartnerProvisioningJob{PartnerKey: "test", RequestHash: "hash", SpecHash: "hash", HQTenantID: 2, TenantID: 1, OwnerUserID: "owner", Status: "ready", State: types.PartnerProvisioningState{AgentIDs: map[string]string{"planner": "planner", "reviewer": "reviewer"}, KnowledgeBaseID: "kb"}}
	if db.Create(&provision).Error != nil {
		t.Fatal("provision setup failed")
	}
	ctx := types.WithCaller(context.Background(), types.Caller{TenantID: 1, UserID: "user", Role: types.TenantRoleViewer})
	ctx = types.WithPrincipal(ctx, types.Principal{Type: types.PrincipalWebUser, ID: "user"})
	s := NewService(db, nil, nil)
	s.Skills = func(context.Context, string, string) (map[string]string, error) {
		return map[string]string{"scene": "fixed", "format": "fixed"}, nil
	}
	s.Render = func(context.Context, string, uint64, Payload, string) (Artifact, error) {
		data := []byte("# 博研文化\n科创课程。\n来源：总部课程")
		return Artifact{Filename: "博研文化.md", MimeType: MimeTypes["md"], Bytes: data, Size: len(data), SHA256: digest(data)}, nil
	}
	s.Review = func(context.Context, Payload, Artifact) (Review, error) {
		return Review{Passed: true, Issues: []Issue{}}, nil
	}
	p := Proposal{Scene: "faq-guide", Format: "md", OutlineMarkdown: "# 科创课程问答\n说明总部已公开课程内容。", Requirements: map[string]string{"audience": "合作伙伴", "topic": "课程"}, Document: Document{Title: "课程问答", Sections: []Section{{Heading: "课程", Body: "博研文化开展科创课程。"}}}, Sources: []SourceRequest{{ChunkID: "chunk"}}}
	return s, ctx, p
}

func proposed(t *testing.T, s *Service, ctx context.Context, p Proposal) *Job {
	t.Helper()
	job, err := s.Propose(ctx, "session", p)
	if err != nil {
		t.Fatal(err)
	}
	return job
}

func confirmed(t *testing.T, s *Service, ctx context.Context, p Proposal) *Job {
	t.Helper()
	job := proposed(t, s, ctx, p)
	job, err := s.Confirm(ctx, "session", job.ID, job.OutlineHash)
	if err != nil {
		t.Fatal(err)
	}
	return job
}

func TestConfirmationCannotBeBypassed(t *testing.T) {
	s, ctx, p := fixture(t)
	job := proposed(t, s, ctx, p)
	s.Render = func(context.Context, string, uint64, Payload, string) (Artifact, error) {
		t.Fatal("renderer called before confirmation")
		return Artifact{}, nil
	}
	if _, err := s.Generate(ctx, "session", job.ID, job.OutlineHash); !errors.Is(err, ErrConflict) {
		t.Fatalf("got %v", err)
	}
	if _, err := s.Confirm(ctx, "session", job.ID, "wrong"); !errors.Is(err, ErrConflict) {
		t.Fatalf("got %v", err)
	}
	if _, err := s.Download(ctx, "session", job.ID); !errors.Is(err, ErrConflict) {
		t.Fatalf("got %v", err)
	}
}

func TestDocumentScopeIsolation(t *testing.T) {
	s, ctx, p := fixture(t)
	job := proposed(t, s, ctx, p)
	for _, caller := range []types.Caller{{TenantID: 2, UserID: "user"}, {TenantID: 1, UserID: "other"}} {
		other := types.WithCaller(ctx, caller)
		for _, call := range []func() error{
			func() error { _, err := s.Get(other, "session", job.ID); return err },
			func() error { _, err := s.Confirm(other, "session", job.ID, job.OutlineHash); return err },
			func() error { _, err := s.Generate(other, "session", job.ID, job.OutlineHash); return err },
			func() error { _, err := s.Download(other, "session", job.ID); return err },
		} {
			if !errors.Is(call(), ErrDenied) {
				t.Fatal("scope bypass")
			}
		}
	}
	if _, err := s.Get(ctx, "second-session", job.ID); !errors.Is(err, ErrDenied) {
		t.Fatal("cross-session access")
	}
	api := types.WithPrincipal(ctx, types.Principal{Type: types.PrincipalAPITenant, ID: "key"})
	if _, err := s.Confirm(api, "session", job.ID, job.OutlineHash); !errors.Is(err, ErrDenied) {
		t.Fatal("machine confirmation accepted")
	}
}

func TestNewOutlineInvalidatesApproval(t *testing.T) {
	s, ctx, p := fixture(t)
	old := confirmed(t, s, ctx, p)
	p.OutlineMarkdown += "\n增加来源说明"
	latest := proposed(t, s, ctx, p)
	if latest.OutlineHash == old.OutlineHash {
		t.Fatal("outline hash unchanged")
	}
	if _, err := s.Generate(ctx, "session", old.ID, old.OutlineHash); !errors.Is(err, ErrConflict) {
		t.Fatal("old approval reused")
	}
	if _, err := s.Generate(ctx, "session", latest.ID, old.OutlineHash); !errors.Is(err, ErrConflict) {
		t.Fatal("cross-revision approval reused")
	}
}

func TestDifferentFormatsKeepTheirConfirmations(t *testing.T) {
	s, ctx, p := fixture(t)
	md := confirmed(t, s, ctx, p)
	p.Format = "docx"
	docx := proposed(t, s, ctx, p)
	stored, err := s.Get(ctx, "session", md.ID)
	if err != nil || stored.State != Approved || stored.ConfirmedAt == nil {
		t.Fatal("new format invalidated another format")
	}
	if docx.State != AwaitingConfirmation {
		t.Fatal("new format skipped confirmation")
	}
}

func TestMissingBudgetValuesAreNotZero(t *testing.T) {
	for _, input := range []string{`{"label":"课程"}`, `{"label":"课程","quantity":2}`, `{"label":"课程","unit_price":10}`, `{"label":"课程","quantity":null,"unit_price":10}`, `{"label":"课程","quantity":2,"unit_price":null}`} {
		var item BudgetItem
		if json.Unmarshal([]byte(input), &item) == nil {
			t.Fatalf("missing budget value accepted: %s", input)
		}
	}
	var item BudgetItem
	if err := json.Unmarshal([]byte(`{"label":"明确免费","quantity":1,"unit_price":0}`), &item); err != nil || item.UnitPrice != 0 {
		t.Fatal("explicit zero rejected")
	}
}

func TestReviewOwnsPublicationGate(t *testing.T) {
	s, ctx, p := fixture(t)
	job := confirmed(t, s, ctx, p)
	s.Review = func(ctx context.Context, p Payload, a Artifact) (Review, error) {
		if _, err := s.Download(ctx, "session", job.ID); !errors.Is(err, ErrConflict) {
			t.Fatal("candidate downloadable")
		}
		return Review{Passed: true, Issues: []Issue{}}, nil
	}
	result, err := s.Generate(ctx, "session", job.ID, job.OutlineHash)
	if err != nil || result.State != Published {
		t.Fatalf("publish failed: %v", err)
	}
	file, err := s.Download(ctx, "session", job.ID)
	if err != nil || len(file.Artifact) == 0 {
		t.Fatal("approved artifact missing")
	}
	public, _ := json.Marshal(Public(*file))
	var fields map[string]interface{}
	_ = json.Unmarshal(public, &fields)
	if fields["artifact"] != nil || fields["payload"] != nil || fields["tenant_id"] != nil {
		t.Fatal("private data exposed")
	}
}

func TestFailedReviewsNeverPublish(t *testing.T) {
	cases := []struct {
		name   string
		review Review
		err    error
		state  string
	}{
		{"timeout", Review{}, context.DeadlineExceeded, Failed},
		{"missing_issues", Review{Passed: true}, nil, Failed},
		{"blocking", Review{Passed: true, Issues: []Issue{{Location: "预算", Problem: "无价格来源", Severity: "blocking", Suggestion: "核实总部价格"}}}, nil, Blocked},
		{"reject", Review{Passed: false, Issues: []Issue{}}, nil, Blocked},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s, ctx, p := fixture(t)
			job := confirmed(t, s, ctx, p)
			s.Review = func(context.Context, Payload, Artifact) (Review, error) { return tc.review, tc.err }
			_, _ = s.Generate(ctx, "session", job.ID, job.OutlineHash)
			stored, err := s.Get(ctx, "session", job.ID)
			if err != nil || stored.State != tc.state || len(stored.Artifact) != 0 {
				t.Fatalf("unsafe state %v %v", stored, err)
			}
			if _, err := s.Download(ctx, "session", job.ID); err == nil {
				t.Fatal("failed review published")
			}
		})
	}
}

func TestSourceChangesAndRevocationStopPublication(t *testing.T) {
	for _, change := range []string{"content", "disabled", "deleted", "agent_scope", "share_scope"} {
		t.Run(change, func(t *testing.T) {
			s, ctx, p := fixture(t)
			job := confirmed(t, s, ctx, p)
			s.Review = func(context.Context, Payload, Artifact) (Review, error) {
				switch change {
				case "content":
					s.DB.Exec("UPDATE chunks SET content = 'changed'")
				case "disabled":
					s.DB.Exec("UPDATE chunks SET is_enabled = false")
				case "deleted":
					s.DB.Exec("UPDATE knowledges SET deleted_at = CURRENT_TIMESTAMP")
				case "agent_scope":
					s.DB.Model(&types.CustomAgent{}).Where("id = ?", "planner").Update("config", types.CustomAgentConfig{KBSelectionMode: "none"})
				case "share_scope":
					s.DB.Exec("UPDATE knowledge_bases SET tenant_id = 9")
				}
				return Review{Passed: true, Issues: []Issue{}}, nil
			}
			if _, err := s.Generate(ctx, "session", job.ID, job.OutlineHash); err == nil {
				t.Fatal("stale source published")
			}
			if _, err := s.Download(ctx, "session", job.ID); err == nil {
				t.Fatal("stale source downloaded")
			}
		})
	}
}

func TestInvalidArtifactFailsClosed(t *testing.T) {
	s, ctx, p := fixture(t)
	job := confirmed(t, s, ctx, p)
	s.Render = func(context.Context, string, uint64, Payload, string) (Artifact, error) {
		return Artifact{Filename: "fake.md", MimeType: MimeTypes["md"], SHA256: "wrong", Bytes: []byte("fake"), Size: 4}, nil
	}
	s.Review = func(context.Context, Payload, Artifact) (Review, error) {
		t.Fatal("invalid artifact reviewed")
		return Review{}, nil
	}
	if _, err := s.Generate(ctx, "session", job.ID, job.OutlineHash); err == nil {
		t.Fatal("invalid artifact accepted")
	}
	stored, _ := s.Get(ctx, "session", job.ID)
	if stored.State != Failed || len(stored.Artifact) != 0 {
		t.Fatal("invalid artifact retained")
	}
}

func TestStrictReviewParser(t *testing.T) {
	for _, input := range []string{`{}`, `{"passed":true}`, `{"passed":"true","issues":[]}`, `{"passed":true,"issues":[]} garbage`, `{"passed":true,"issues":[],"approved":true}`, "```json\n{}\n```", `{"passed":true,"issues":[{"location":"a","problem":"b","severity":"unknown","suggestion":"c"}]}`} {
		if _, err := ParseReview(input); err == nil {
			t.Fatalf("invalid review accepted: %s", input)
		}
	}
	if result, err := ParseReview(`{"passed":true,"issues":[]}`); err != nil || !result.Passed {
		t.Fatal("valid review rejected")
	}
}

func TestRequiredBusinessFields(t *testing.T) {
	_, _, p := fixture(t)
	delete(p.Requirements, "audience")
	if ValidateProposal(p) == nil {
		t.Fatal("missing fields accepted")
	}
	p.Requirements["audience"] = "合作伙伴"
	p.Sources = nil
	if ValidateProposal(p) == nil {
		t.Fatal("unsourced proposal accepted")
	}
}
