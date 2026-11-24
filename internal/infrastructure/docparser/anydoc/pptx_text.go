package anydoc

import (
	"archive/zip"
	"bytes"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"path"
	"strings"
)

const (
	maxPPTXPart = 8 << 20
	maxPPTXXML  = 32 << 20
	officeNS    = "http://schemas.openxmlformats.org/"
	strictNS    = "http://purl.oclc.org/ooxml/"
)

// pptxParts only opens named XML parts referenced by the presentation. Media,
// external relationships, masters and unrelated embedded documents stay closed.
type pptxParts struct {
	*zip.Reader
	remaining int64
}

func (p *pptxParts) read(name string) ([]byte, error) {
	f, err := p.Open(name)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	limit := min(int64(maxPPTXPart), p.remaining)
	b, err := io.ReadAll(io.LimitReader(f, limit+1))
	if err != nil {
		return nil, err
	}
	if int64(len(b)) > limit {
		return nil, errors.New("presentation XML exceeds the text extraction budget")
	}
	p.remaining -= int64(len(b))
	return b, nil
}

// targets returns only internal references of the requested type. Check the
// resolved path before opening it, even if an input lies about its MIME type.
func (p *pptxParts) targets(source, kind, dir string) (map[string]string, error) {
	b, err := p.read(path.Join(path.Dir(source), "_rels", path.Base(source)+".rels"))
	if errors.Is(err, fs.ErrNotExist) && kind == "notesSlide" {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var rels struct {
		Items []struct {
			ID   string `xml:"Id,attr"`
			Type string `xml:"Type,attr"`
			To   string `xml:"Target,attr"`
			Mode string `xml:"TargetMode,attr"`
		} `xml:"Relationship"`
	}
	if err := xml.Unmarshal(b, &rels); err != nil {
		return nil, err
	}
	out := make(map[string]string)
	for _, r := range rels.Items {
		if r.Type != officeNS+"officeDocument/2006/relationships/"+kind && r.Type != strictNS+"officeDocument/relationships/"+kind {
			continue
		}
		name := path.Join(path.Dir(source), r.To)
		if strings.HasPrefix(r.To, "/") {
			name = path.Clean(r.To[1:])
		}
		if r.Mode == "External" || strings.ContainsAny(r.To, "\\:?#") || path.Dir(name) != dir || path.Ext(name) != ".xml" || r.ID == "" || out[r.ID] != "" {
			return nil, fmt.Errorf("invalid %s relationship in %s", kind, source)
		}
		out[r.ID] = name
	}
	return out, nil
}

func pptxText(data []byte) (string, error) {
	z, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return "", err
	}
	if len(z.File) > 10000 {
		return "", errors.New("too many presentation parts")
	}
	p := pptxParts{z, maxPPTXXML}
	const source = "ppt/presentation.xml"
	b, err := p.read("[Content_Types].xml")
	if err != nil {
		return "", err
	}
	var manifest struct {
		Parts []struct {
			Name string `xml:"PartName,attr"`
			MIME string `xml:"ContentType,attr"`
		} `xml:"Override"`
	}
	if err := xml.Unmarshal(b, &manifest); err != nil {
		return "", err
	}
	isPPTX := false
	for _, part := range manifest.Parts {
		if part.Name == "/"+source && part.MIME == "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml" {
			isPPTX = true
			break
		}
	}
	if !isPPTX {
		return "", errors.New("package has no PPTX presentation main part")
	}
	b, err = p.read(source)
	if err != nil {
		return "", err
	}
	var pres struct {
		XMLName xml.Name `xml:"presentation"`
		Slides  []struct {
			Attrs []xml.Attr `xml:",any,attr"`
		} `xml:"sldIdLst>sldId"`
	}
	if err := xml.Unmarshal(b, &pres); err != nil {
		return "", err
	}
	if !presentationName(pres.XMLName, "presentation") || len(pres.Slides) == 0 || len(pres.Slides) > 1000 {
		return "", errors.New("invalid presentation or excessive slide count")
	}
	slides, err := p.targets(source, "slide", "ppt/slides")
	if err != nil {
		return "", err
	}
	var md strings.Builder
	seen := make(map[string]bool)
	for i, slide := range pres.Slides {
		var name string
		for _, a := range slide.Attrs {
			if a.Name.Local == "id" && (a.Name.Space == officeNS+"officeDocument/2006/relationships" || a.Name.Space == strictNS+"officeDocument/relationships") {
				name = slides[a.Value]
				break
			}
		}
		if name == "" || seen[name] {
			return "", fmt.Errorf("invalid slide relationship on page %d", i+1)
		}
		seen[name] = true
		body, err := p.text(name, false)
		if err != nil {
			return "", err
		}
		targets, err := p.targets(name, "notesSlide", "ppt/notesSlides")
		if err != nil {
			return "", err
		}
		if len(targets) > 1 {
			return "", fmt.Errorf("multiple notes parts on page %d", i+1)
		}
		var notes string
		for _, target := range targets {
			notes, err = p.text(target, true)
			if err != nil {
				return "", err
			}
		}
		if body == "" && notes == "" {
			continue
		}
		fmt.Fprintf(&md, "## 第 %d 页\n\n", i+1)
		if body != "" {
			fmt.Fprintf(&md, "%s\n\n", body)
		}
		if notes != "" {
			fmt.Fprintf(&md, "演讲备注\n\n%s\n\n", notes)
		}
	}
	if md.Len() == 0 {
		return "", errors.New("presentation has no extractable slide text or speaker notes")
	}
	return strings.TrimSpace(md.String()), nil
}

func presentationName(n xml.Name, local string) bool {
	return n.Local == local && (n.Space == officeNS+"presentationml/2006/main" || n.Space == strictNS+"presentationml/main")
}

func (p *pptxParts) text(name string, notes bool) (string, error) {
	b, err := p.read(name)
	if err != nil {
		return "", err
	}
	d := xml.NewDecoder(bytes.NewReader(b))
	var out strings.Builder
	inText := !notes
	root := "sld"
	if notes {
		root = "notes"
	}
	foundRoot := false
	for {
		t, err := d.Token()
		if err == io.EOF {
			if !foundRoot {
				return "", fmt.Errorf("missing %s element in %s", root, name)
			}
			return strings.TrimSpace(out.String()), nil
		}
		if err != nil {
			return "", err
		}
		switch e := t.(type) {
		case xml.StartElement:
			if !foundRoot {
				if !presentationName(e.Name, root) {
					return "", fmt.Errorf("unexpected root element in %s", name)
				}
				foundRoot = true
			}
			if notes && presentationName(e.Name, "sp") {
				inText = true
			}
			if notes && presentationName(e.Name, "ph") {
				// A notes page also carries a slide thumbnail, page number,
				// header and footer. Only the body placeholder is speech.
				inText = false
				for _, a := range e.Attr {
					if a.Name.Local == "type" && a.Value == "body" {
						inText = true
					}
				}
			}
			if !inText || (e.Name.Space != officeNS+"drawingml/2006/main" && e.Name.Space != strictNS+"drawingml/main") {
				continue
			}
			switch e.Name.Local {
			case "t":
				var s string
				if err := d.DecodeElement(&s, &e); err != nil {
					return "", err
				}
				out.WriteString(s)
			case "br", "p":
				if out.Len() > 0 {
					out.WriteByte('\n')
				}
			}
		case xml.EndElement:
			if notes && presentationName(e.Name, "sp") {
				inText = false
			}
		}
	}
}
