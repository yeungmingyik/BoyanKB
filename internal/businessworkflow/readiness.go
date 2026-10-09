package businessworkflow

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"os"
	"slices"
	"strings"
	"time"

	"github.com/Tencent/WeKnora/internal/boyanskills"
)

func Readiness(ctx context.Context) (map[string]interface{}, error) {
	selectedPPT, err := selectedTemplateVersion("pptx")
	if err != nil {
		return nil, err
	}
	names := []string{"courseware", "event-plan", "local-promotion-plan", "cooperation-proposal", "faq-guide", "docx", "pptx", "xlsx", "md"}
	for _, name := range names {
		if _, err := boyanskills.Fixed("boyan-" + name); err != nil {
			return nil, ErrUnavailable
		}
	}
	endpoint := strings.TrimRight(os.Getenv("BOYAN_DOCUMENT_RENDERER_URL"), "/")
	token := os.Getenv("BOYAN_DOCUMENT_RENDERER_TOKEN")
	u, err := url.Parse(endpoint)
	if err != nil || token == "" || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") {
		return nil, ErrUnavailable
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint+"/health", nil)
	if err != nil {
		return nil, ErrUnavailable
	}
	req.Header.Set("Authorization", "Bearer "+token)
	client := &http.Client{Timeout: 10 * time.Second, Transport: &http.Transport{Proxy: nil}, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	resp, err := client.Do(req)
	if err != nil {
		return nil, ErrUnavailable
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, ErrUnavailable
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, 65537))
	if err != nil || len(body) > 65536 {
		return nil, ErrUnavailable
	}
	var health struct {
		Status            string              `json:"status"`
		TemplateVersion   string              `json:"template_version"`
		TemplateVersions  map[string][]string `json:"template_versions"`
		PPTVersions       []string            `json:"ppt_template_versions"`
		Formats           []string            `json:"formats"`
		TemplateIntegrity bool                `json:"template_integrity"`
	}
	if json.Unmarshal(body, &health) != nil || health.Status != "ok" || health.TemplateVersion != "boyan-v1" || !health.TemplateIntegrity {
		return nil, ErrUnavailable
	}
	for _, format := range []string{"docx", "pptx", "xlsx", "md"} {
		if !slices.Contains(health.Formats, format) {
			return nil, ErrUnavailable
		}
	}
	if selectedPPT != templateV1 && (!slices.Contains(health.TemplateVersions[selectedPPT], "pptx") || !slices.Contains(health.PPTVersions, selectedPPT)) {
		return nil, ErrUnavailable
	}
	if health.TemplateVersions == nil {
		health.TemplateVersions = map[string][]string{templateV1: health.Formats}
	}
	if health.PPTVersions == nil {
		health.PPTVersions = []string{templateV1}
	}
	return map[string]interface{}{"ready": true, "template_version": health.TemplateVersion, "template_versions": health.TemplateVersions, "ppt_template_versions": health.PPTVersions, "selected_ppt_template_version": selectedPPT, "template_integrity": true, "formats": health.Formats, "restricted_skill_count": len(names)}, nil
}
