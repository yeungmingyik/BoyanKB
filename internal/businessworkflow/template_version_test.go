package businessworkflow

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestTemplateVersionSelection(t *testing.T) {
	for _, tc := range []struct {
		configured, format, expected string
	}{
		{"", "pptx", templateV1}, {templateV1, "pptx", templateV1},
		{templateV2, "pptx", templateV2}, {templateV2, "docx", templateV1},
		{templateV2, "xlsx", templateV1}, {templateV2, "md", templateV1},
		{"boyan-v3", "pptx", ""}, {"../boyan-v1", "pptx", ""},
	} {
		t.Run(tc.configured+"/"+tc.format, func(t *testing.T) {
			t.Setenv("BOYAN_PPT_TEMPLATE_VERSION", tc.configured)
			actual, err := selectedTemplateVersion(tc.format)
			if actual != tc.expected || (tc.expected == "") != errors.Is(err, ErrUnavailable) {
				t.Fatalf("version=%q err=%v", actual, err)
			}
		})
	}
	for _, tc := range []struct{ version, format string }{{templateV2, "docx"}, {"boyan-v3", "pptx"}, {templateV1, "unknown"}} {
		if supportedTemplateVersion(tc.version, tc.format) {
			t.Fatal("unsupported template accepted")
		}
	}
}

func TestProposalPinsTemplateVersionAndRejectsUnknownBeforeWriting(t *testing.T) {
	s, ctx, p := fixture(t)
	p.Format = "pptx"
	t.Setenv("BOYAN_PPT_TEMPLATE_VERSION", templateV1)
	old := confirmed(t, s, ctx, p)
	t.Setenv("BOYAN_PPT_TEMPLATE_VERSION", templateV2)
	rendered := false
	s.Render = func(_ context.Context, _ string, _ uint64, payload Payload, _ string) (Artifact, error) {
		rendered = true
		if payload.TemplateVersion != templateV1 {
			t.Fatal("existing approval changed template")
		}
		return Artifact{}, ErrUnavailable
	}
	if _, err := s.Generate(ctx, "session", old.ID, old.OutlineHash); !errors.Is(err, ErrUnavailable) {
		t.Fatal(err)
	}
	if !rendered {
		t.Fatal("old approved template did not reach renderer")
	}
	latest := proposed(t, s, ctx, p)
	var payload Payload
	if json.Unmarshal(latest.Payload, &payload) != nil || payload.TemplateVersion != templateV2 || latest.OutlineHash == old.OutlineHash {
		t.Fatal("new template was not bound to approval hash")
	}
	t.Setenv("BOYAN_PPT_TEMPLATE_VERSION", "unregistered")
	if _, err := s.Propose(ctx, "session", p); !errors.Is(err, ErrUnavailable) {
		t.Fatal("unknown template accepted")
	}
	var count int64
	s.DB.Model(&Job{}).Count(&count)
	if count != 2 {
		t.Fatal("invalid configuration created a job")
	}
	current, err := s.Get(ctx, "session", latest.ID)
	if err != nil || current.State != AwaitingConfirmation {
		t.Fatal("invalid configuration superseded valid proposal")
	}
}

func TestPublishedV1DownloadSurvivesTemplateRollout(t *testing.T) {
	s, ctx, p := fixture(t)
	p.Format = "pptx"
	data := zipDocument(t, map[string]string{
		"ppt/presentation.xml":            `<presentation xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sldIdLst><sldId id="1" r:id="slide1"/></sldIdLst></presentation>`,
		"ppt/_rels/presentation.xml.rels": `<Relationships><Relationship Id="slide1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/></Relationships>`,
		"ppt/slides/slide1.xml":           `<slide><p><r><t>博研文化课程</t></r></p></slide>`,
	})
	s.Render = func(context.Context, string, uint64, Payload, string) (Artifact, error) {
		return Artifact{Filename: "课程.pptx", MimeType: MimeTypes["pptx"], Bytes: data, Size: len(data), SHA256: digest(data)}, nil
	}
	t.Setenv("BOYAN_PPT_TEMPLATE_VERSION", templateV1)
	job := confirmed(t, s, ctx, p)
	job, err := s.Generate(ctx, "session", job.ID, job.OutlineHash)
	if err != nil || job.State != Published {
		t.Fatal("publication failed")
	}
	before := job.ArtifactSHA256
	t.Setenv("BOYAN_PPT_TEMPLATE_VERSION", templateV2)
	s.Render = func(context.Context, string, uint64, Payload, string) (Artifact, error) {
		t.Fatal("download invoked renderer")
		return Artifact{}, ErrUnavailable
	}
	got, err := s.Download(ctx, "session", job.ID)
	if err != nil || got.ArtifactSHA256 != before {
		t.Fatal("rollout invalidated archived bytes")
	}
}

func TestReadinessRequiresSelectedPPTCapability(t *testing.T) {
	t.Setenv("BOYAN_PRESETS_ROOT", "../../presets/boyan/v1")
	t.Setenv("BOYAN_SKILLS_ROOT", "../../skills/boyan")
	t.Setenv("BOYAN_DOCUMENT_RENDERER_TOKEN", "test-token")
	for _, tc := range []struct {
		name, selected string
		capabilities   bool
		pptListed      bool
		wantReady      bool
	}{
		{"legacy", templateV1, false, false, true},
		{"missing-v2", templateV2, false, false, false},
		{"missing-list", templateV2, true, false, false},
		{"both", templateV2, true, true, true},
		{"unknown", "unknown", true, true, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("BOYAN_PPT_TEMPLATE_VERSION", tc.selected)
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != "/health" || r.Header.Get("Authorization") != "Bearer test-token" {
					t.Error("invalid health request")
				}
				health := map[string]interface{}{"status": "ok", "template_version": templateV1, "template_integrity": true, "formats": []string{"docx", "pptx", "xlsx", "md"}}
				if tc.capabilities {
					health["template_versions"] = map[string][]string{templateV1: {"docx", "pptx", "xlsx", "md"}, templateV2: {"pptx"}}
				}
				if tc.pptListed {
					health["ppt_template_versions"] = []string{templateV1, templateV2}
				}
				_ = json.NewEncoder(w).Encode(health)
			}))
			defer server.Close()
			t.Setenv("BOYAN_DOCUMENT_RENDERER_URL", server.URL)
			result, err := Readiness(context.Background())
			if (err == nil) != tc.wantReady {
				t.Fatalf("ready=%v err=%v", tc.wantReady, err)
			}
			if err == nil && result["selected_ppt_template_version"] != tc.selected {
				t.Fatal("wrong selected capability")
			}
		})
	}
}
