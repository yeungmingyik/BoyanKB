package anydoc

import (
	"archive/zip"
	"bytes"
	"strings"
	"testing"
)

func TestDOCXTextPackageKeepsWordPartsWithoutMedia(t *testing.T) {
	parts := map[string]string{"[Content_Types].xml": "<Types/>", "word/document.xml": "<document/>",
		"word/numbering.xml": "<numbering/>", "word/media/illustration.svg": "media",
		"word/embeddings/object.bin": "object"}
	data, err := docxWithoutMedia(pptxArchive(t, parts))
	if err != nil {
		t.Fatal(err)
	}
	z, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil || len(z.File) != 3 {
		t.Fatalf("unexpected retained package: %v, %v", z, err)
	}
	for _, f := range z.File {
		if _, ok := parts[f.Name]; !ok || strings.Contains(f.Name, "/media/") || strings.Contains(f.Name, "/embeddings/") {
			t.Fatal("unexpected part", f.Name)
		}
	}
	parts["word/document.xml"] = strings.Repeat("a", 8<<20+1)
	if _, err := docxWithoutMedia(pptxArchive(t, parts)); err == nil {
		t.Fatal("oversized XML accepted")
	}
}
