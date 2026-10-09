package businessworkflow

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"encoding/xml"
	"io"
	"net/http"
	"net/url"
	"os"
	"path"
	"sort"
	"strings"
	"time"
	"unicode/utf8"
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
}

func (a Artifact) Validate(format string) error {
	if len(a.Bytes) == 0 || len(a.Bytes) > maxArtifactSize || a.Size != len(a.Bytes) || digest(a.Bytes) != a.SHA256 || path.Base(a.Filename) != a.Filename || strings.ContainsAny(a.Filename, "\\\r\n\x00") || !strings.HasSuffix(a.Filename, "."+format) || a.MimeType != MimeTypes[format] {
		return ErrInvalid
	}
	_, err := artifactText(a.Bytes, format)
	return err
}

func artifactText(data []byte, format string) (string, error) {
	if format == "md" {
		if !utf8.Valid(data) || len(data) > 512*1024 {
			return "", ErrInvalid
		}
		return string(data), nil
	}
	z, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil || len(z.File) > 2000 {
		return "", ErrInvalid
	}
	required := map[string]string{"docx": "word/document.xml", "pptx": "ppt/presentation.xml", "xlsx": "xl/workbook.xml"}[format]
	if required == "" {
		return "", ErrInvalid
	}
	found := false
	var total uint64
	parts := map[string]string{}
	for _, file := range z.File {
		if file.Name == required {
			found = true
		}
		if strings.Contains(file.Name, "..") || strings.HasSuffix(strings.ToLower(file.Name), "vbaproject.bin") || strings.Contains(strings.ToLower(file.Name), "externallinks/") {
			return "", ErrInvalid
		}
		total += file.UncompressedSize64
		if total > 64*1024*1024 || file.UncompressedSize64 > 16*1024*1024 {
			return "", ErrInvalid
		}
		if !strings.HasSuffix(file.Name, ".xml") && !strings.HasSuffix(file.Name, ".rels") {
			continue
		}
		r, err := file.Open()
		if err != nil {
			return "", ErrInvalid
		}
		body, err := io.ReadAll(io.LimitReader(r, 16*1024*1024+1))
		r.Close()
		if err != nil || len(body) > 16*1024*1024 {
			return "", ErrInvalid
		}
		decoder := xml.NewDecoder(bytes.NewReader(body))
		var text strings.Builder
		for {
			token, err := decoder.Token()
			if err == io.EOF {
				break
			}
			if err != nil {
				return "", ErrInvalid
			}
			switch value := token.(type) {
			case xml.StartElement:
				for _, attr := range value.Attr {
					if attr.Name.Local == "TargetMode" && attr.Value == "External" {
						return "", ErrInvalid
					}
				}
			case xml.CharData:
				v := strings.TrimSpace(string(value))
				if v != "" {
					text.WriteString(v)
					text.WriteByte('\n')
				}
			}
		}
		if strings.HasPrefix(file.Name, "word/") || strings.HasPrefix(file.Name, "ppt/slides/") || strings.HasPrefix(file.Name, "ppt/notesSlides/") || strings.HasPrefix(file.Name, "xl/worksheets/") || file.Name == "xl/sharedStrings.xml" {
			parts[file.Name] = text.String()
		}
	}
	if !found {
		return "", ErrInvalid
	}
	keys := make([]string, 0, len(parts))
	for key := range parts {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	var text strings.Builder
	for _, key := range keys {
		text.WriteString(parts[key])
	}
	if text.Len() == 0 || text.Len() > 512*1024 {
		return "", ErrInvalid
	}
	return text.String(), nil
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
		Success    bool     `json:"success"`
		Artifact   Artifact `json:"artifact"`
		Validation struct {
			Passed bool     `json:"passed"`
			Checks []string `json:"checks"`
		} `json:"validation"`
	}
	if json.Unmarshal(data, &result) != nil || !result.Success || !result.Validation.Passed || len(result.Validation.Checks) == 0 {
		return Artifact{}, ErrInvalid
	}
	result.Artifact.Bytes, err = base64.StdEncoding.DecodeString(result.Artifact.ContentBase64)
	result.Artifact.ContentBase64 = ""
	if err != nil || result.Artifact.Validate(p.Format) != nil {
		return Artifact{}, ErrInvalid
	}
	return result.Artifact, nil
}
