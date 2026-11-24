package anydoc

import (
	"archive/zip"
	"bytes"
	"fmt"
	"strings"
	"testing"
)

func textOnlyFixture() map[string]string {
	const namespaces = ` xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"`
	const rel = officeNS + "officeDocument/2006/relationships/"
	return map[string]string{
		"[Content_Types].xml":  `<Types><Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/></Types>`,
		"ppt/presentation.xml": `<p:presentation` + namespaces + `><p:sldIdLst><p:sldId id="256" r:id="image"/><p:sldId id="257" r:id="words"/><p:sldId id="258" r:id="last"/></p:sldIdLst></p:presentation>`,
		"ppt/_rels/presentation.xml.rels": fmt.Sprintf(`<Relationships>
<Relationship Id="last" Type="%[1]sslide" Target="slides/slide2.xml"/>
<Relationship Id="image" Type="%[1]sslide" Target="slides/slide3.xml"/>
<Relationship Id="words" Type="%[1]sslide" Target="/ppt/slides/slide10.xml"/>
</Relationships>`, rel),
		"ppt/slides/slide3.xml":             `<p:sld` + namespaces + `><p:pic><a:blip r:embed="picture"/></p:pic></p:sld>`,
		"ppt/slides/slide10.xml":            `<p:sld` + namespaces + `><p:sp><a:p><a:r><a:t>hello</a:t></a:r><a:r><a:t> world</a:t></a:r></a:p></p:sp></p:sld>`,
		"ppt/slides/slide2.xml":             `<p:sld` + namespaces + `><p:graphicFrame><a:tc><a:p><a:r><a:t>table cell</a:t></a:r></a:p></a:tc></p:graphicFrame></p:sld>`,
		"ppt/slides/_rels/slide10.xml.rels": fmt.Sprintf(`<Relationships><Relationship Id="r1" Type="%[1]snotesSlide" Target="../notesSlides/notesSlide4.xml"/><Relationship Id="r2" Type="%[1]shyperlink" TargetMode="External" Target="https://example.invalid"/></Relationships>`, rel),
		"ppt/notesSlides/notesSlide4.xml":   `<p:notes` + namespaces + `><p:sp><p:ph type="sldNum"/><a:p><a:t>ignore footer</a:t></a:p></p:sp><p:sp><p:ph type="body"/><a:p><a:t>spoken words</a:t></a:p></p:sp></p:notes>`,
	}
}

func pptxArchive(t *testing.T, parts map[string]string) []byte {
	t.Helper()
	var b bytes.Buffer
	z := zip.NewWriter(&b)
	for name, content := range parts {
		f, err := z.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := f.Write([]byte(content)); err != nil {
			t.Fatal(err)
		}
	}
	// This media entry has no usable decompressor. Opening it would fail.
	_, err := z.CreateRaw(&zip.FileHeader{Name: "ppt/media/picture.jpg", Method: 255})
	if err != nil {
		t.Fatal(err)
	}
	if err := z.Close(); err != nil {
		t.Fatal(err)
	}
	return b.Bytes()
}

func TestPPTXTextKeepsSourcePageOrderAndRealNotes(t *testing.T) {
	text, err := pptxText(pptxArchive(t, textOnlyFixture()))
	const want = "## 第 2 页\n\nhello world\n\n演讲备注\n\nspoken words\n\n## 第 3 页\n\ntable cell"
	if err != nil || text != want {
		t.Fatalf("got %q, %v; want %q", text, err, want)
	}
}

func TestPPTXTextRejectsInvalidInputAndOversizedXML(t *testing.T) {
	const rels = "ppt/slides/_rels/slide10.xml.rels"
	for _, tc := range []struct {
		name   string
		change func(map[string]string)
	}{
		{"external notes", func(p map[string]string) {
			p[rels] = strings.Replace(p[rels], `Id="r1"`, `Id="r1" TargetMode="External"`, 1)
		}},
		{"misdirected notes", func(p map[string]string) {
			p[rels] = strings.Replace(p[rels], "../notesSlides/notesSlide4.xml", "../media/something.xml", 1)
		}},
		{"malformed slide", func(p map[string]string) { p["ppt/slides/slide2.xml"] = "<slide>" }},
		{"macro presentation", func(p map[string]string) {
			p["[Content_Types].xml"] = strings.ReplaceAll(p["[Content_Types].xml"], "openxmlformats-officedocument.presentationml.presentation.main", "ms-powerpoint.presentation.macroEnabled.main")
		}},
		{"large XML", func(p map[string]string) { p["ppt/slides/slide2.xml"] = strings.Repeat("a", maxPPTXPart+1) }},
		{"image-only", func(p map[string]string) {
			p["ppt/slides/slide2.xml"] = p["ppt/slides/slide3.xml"]
			p["ppt/slides/slide10.xml"] = p["ppt/slides/slide3.xml"]
			delete(p, rels)
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			p := textOnlyFixture()
			tc.change(p)
			if text, err := pptxText(pptxArchive(t, p)); err == nil || text != "" {
				t.Fatalf("expected extraction to fail, got %q, %v", text, err)
			}
		})
	}
}
