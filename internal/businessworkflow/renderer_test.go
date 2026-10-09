package businessworkflow

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestRendererTransportRejectsRedirectAndInvalidValidation(t *testing.T) {
	for _, mode := range []string{"redirect", "not_validated", "wrong_digest", "valid"} {
		t.Run(mode, func(t *testing.T) {
			data := []byte("# 博研文化\n已验证正文")
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Header.Get("Authorization") != "Bearer test-only-secret" || r.URL.Path != "/render" || r.Method != http.MethodPost {
					t.Error("invalid renderer request")
				}
				if mode == "redirect" {
					w.Header().Set("Location", "http://127.0.0.1:1/secret")
					w.WriteHeader(http.StatusFound)
					return
				}
				artifact := Artifact{Filename: "document.md", MimeType: MimeTypes["md"], SHA256: digest(data), Size: len(data), ContentBase64: base64.StdEncoding.EncodeToString(data)}
				if mode == "wrong_digest" {
					artifact.SHA256 = "wrong"
				}
				json.NewEncoder(w).Encode(map[string]interface{}{"success": true, "artifact": artifact, "validation": map[string]interface{}{"passed": mode != "not_validated", "checks": []string{"request_schema", "template_sha256", "utf8_roundtrip"}, "requires_visual_review": true}})
			}))
			defer server.Close()
			t.Setenv("BOYAN_DOCUMENT_RENDERER_URL", server.URL)
			t.Setenv("BOYAN_DOCUMENT_RENDERER_TOKEN", "test-only-secret")
			_, err := renderHTTP(context.Background(), "job", 1, Payload{Proposal: Proposal{Format: "md", Scene: "faq-guide"}}, "hash")
			if (err == nil) != (mode == "valid") {
				t.Fatalf("unexpected acceptance: %v", err)
			}
		})
	}
}

func zipDocument(t *testing.T, entries map[string]string) []byte {
	t.Helper()
	var data bytes.Buffer
	writer := zip.NewWriter(&data)
	for name, content := range entries {
		file, err := writer.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = file.Write([]byte(content)); err != nil {
			t.Fatal(err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	return data.Bytes()
}

func TestArtifactChecksActualOfficeContainer(t *testing.T) {
	valid := map[string]string{"word/document.xml": `<document><p><t>博研文化课程方案</t></p></document>`}
	data := zipDocument(t, valid)
	if text, err := artifactText(data, "docx"); err != nil || text != "博研文化课程方案\n" {
		t.Fatalf("valid document rejected %v", err)
	}
	if _, err := artifactText([]byte("not a docx"), "docx"); err == nil {
		t.Fatal("renamed text accepted")
	}
	if _, err := artifactText(data, "pptx"); err == nil {
		t.Fatal("wrong Office type accepted")
	}
	valid["word/_rels/document.xml.rels"] = `<Relationships><Relationship TargetMode="External" Target="http://metadata/"/></Relationships>`
	if _, err := artifactText(zipDocument(t, valid), "docx"); err == nil {
		t.Fatal("external relationship accepted")
	}
	delete(valid, "word/_rels/document.xml.rels")
	valid["word/vbaProject.bin"] = "macro"
	if _, err := artifactText(zipDocument(t, valid), "docx"); err == nil {
		t.Fatal("macro accepted")
	}
}

func TestArtifactRejectsPathAndCorruption(t *testing.T) {
	data := []byte("# 博研文化")
	for _, name := range []string{"../file.md", `..\file.md`, "file.md\r\nHeader: true", "file.docx"} {
		a := Artifact{Filename: name, MimeType: MimeTypes["md"], Bytes: data, SHA256: digest(data), Size: len(data)}
		if a.Validate("md") == nil {
			t.Fatalf("invalid filename accepted %q", name)
		}
	}
}
