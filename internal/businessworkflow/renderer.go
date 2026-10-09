package businessworkflow

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"os"
	"path"
	"slices"
	"strings"
	"time"
)

const maxArtifactSize = 20 * 1024 * 1024

var MimeTypes = map[string]string{
	"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
	"pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
	"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
	"md":   "text/markdown; charset=utf-8",
}

type Artifact struct {
	Filename      string `json:"filename"`
	MimeType      string `json:"mime_type"`
	SHA256        string `json:"sha256"`
	Size          int    `json:"size"`
	ContentBase64 string `json:"content_base64,omitempty"`
	Bytes         []byte `json:"-"`
	validation    *rendererValidationEvidence
}

type rendererValidationReport struct {
	Passed               bool     `json:"passed"`
	Checks               []string `json:"checks"`
	RequiresVisualReview bool     `json:"requires_visual_review"`
}

type rendererValidationEvidence struct {
	rendererValidationReport
	Authority                string `json:"authority"`
	ArtifactSHA256           string `json:"artifact_sha256"`
	Format                   string `json:"format"`
	InputPerturbationsTested bool   `json:"input_perturbations_tested"`
}

func validatedRendererEvidence(report rendererValidationReport, format string, hasBudget bool, sha256 string) (*rendererValidationEvidence, error) {
	expected := []string{"request_schema", "template_sha256"}
	switch format {
	case "md":
		expected = append(expected, "utf8_roundtrip")
	case "docx":
		expected = append(expected, "office_package_reopened")
	case "pptx":
		expected = append(expected, "office_package_reopened", "text_capacity_bounds", "table_pagination")
	case "xlsx":
		expected = append(expected, "office_package_reopened", "libreoffice_recalculated", "formula_error_scan")
		if hasBudget {
			expected = append(expected, "budget_line_amounts", "budget_total")
		}
	default:
		return nil, ErrInvalid
	}
	if !report.Passed || !report.RequiresVisualReview || len(report.Checks) != len(expected) {
		return nil, ErrInvalid
	}
	seen := make(map[string]bool, len(expected))
	for _, check := range report.Checks {
		if seen[check] || !slices.Contains(expected, check) {
			return nil, ErrInvalid
		}
		seen[check] = true
	}
	return &rendererValidationEvidence{
		rendererValidationReport: rendererValidationReport{Passed: true, Checks: expected, RequiresVisualReview: true},
		Authority:                "configured_internal_renderer", ArtifactSHA256: sha256, Format: format,
	}, nil
}

func (a Artifact) Validate(format string) error {
	if len(a.Bytes) == 0 || len(a.Bytes) > maxArtifactSize || a.Size != len(a.Bytes) || digest(a.Bytes) != a.SHA256 || path.Base(a.Filename) != a.Filename || strings.ContainsAny(a.Filename, "\\\r\n\x00") || !strings.HasSuffix(a.Filename, "."+format) || a.MimeType != MimeTypes[format] {
		return ErrInvalid
	}
	_, err := artifactText(a.Bytes, format)
	return err
}

func renderHTTP(ctx context.Context, id string, tenant uint64, p Payload, hash string) (Artifact, error) {
	endpoint := strings.TrimRight(os.Getenv("BOYAN_DOCUMENT_RENDERER_URL"), "/")
	token := os.Getenv("BOYAN_DOCUMENT_RENDERER_TOKEN")
	u, err := url.Parse(endpoint)
	if err != nil || token == "" || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") {
		return Artifact{}, ErrUnavailable
	}
	scene := "business"
	if p.Scene == "courseware" {
		scene = "teaching"
	}
	if p.Scene == "event-plan" {
		scene = "operations"
	}
	body, err := json.Marshal(map[string]interface{}{"job_id": id, "tenant_id": tenant, "format": p.Format, "brand_id": "boyan-culture", "template_version": p.TemplateVersion, "scene": scene, "outline_hash": hash, "document": p.Document, "sources": p.VerifiedSources, "budget": p.Budget})
	if err != nil {
		return Artifact{}, ErrInvalid
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint+"/render", bytes.NewReader(body))
	if err != nil {
		return Artifact{}, ErrUnavailable
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 90 * time.Second, Transport: &http.Transport{Proxy: nil}, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	resp, err := client.Do(req)
	if err != nil {
		return Artifact{}, ErrUnavailable
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return Artifact{}, ErrUnavailable
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, 30*1024*1024+1))
	if err != nil || len(data) > 30*1024*1024 {
		return Artifact{}, ErrInvalid
	}
	var result struct {
		Success    bool                     `json:"success"`
		Artifact   Artifact                 `json:"artifact"`
		Validation rendererValidationReport `json:"validation"`
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&result) != nil || !result.Success {
		return Artifact{}, ErrInvalid
	}
	var extra interface{}
	if decoder.Decode(&extra) != io.EOF {
		return Artifact{}, ErrInvalid
	}
	result.Artifact.Bytes, err = base64.StdEncoding.DecodeString(result.Artifact.ContentBase64)
	result.Artifact.ContentBase64 = ""
	if err != nil || result.Artifact.Validate(p.Format) != nil {
		return Artifact{}, ErrInvalid
	}
	result.Artifact.validation, err = validatedRendererEvidence(result.Validation, p.Format, p.Budget != nil, result.Artifact.SHA256)
	if err != nil {
		return Artifact{}, err
	}
	return result.Artifact, nil
}
