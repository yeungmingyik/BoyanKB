package wiki

import (
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"strings"
)

// SML 2.0 stores visible shape, list and table text in content paragraphs
// below slide/data. Ignore style, image URLs, link targets and speaker notes.
func slidesMarkdown(sml string) (string, error) {
	d := xml.NewDecoder(strings.NewReader(sml))
	var out, paragraph strings.Builder
	depth, page, contentDepth, paragraphDepth := 0, 0, 0, 0
	var rootSeen, inSlide, inData, hasText, pageHasText bool
	for {
		token, err := d.Token()
		if err == io.EOF {
			break
		}
		if err != nil {
			return "", errors.New("invalid presentation XML")
		}
		switch v := token.(type) {
		case xml.StartElement:
			depth++
			switch {
			case depth == 1:
				if rootSeen || v.Name.Local != "presentation" {
					return "", errors.New("unexpected presentation XML root")
				}
				rootSeen = true
			case depth == 2 && v.Name.Local == "slide":
				page++
				inSlide, pageHasText = true, false
			case inSlide && depth == 3 && v.Name.Local == "data":
				inData = true
			case inData && v.Name.Local == "content":
				contentDepth = depth
			case contentDepth > 0 && v.Name.Local == "p":
				paragraphDepth = depth
				paragraph.Reset()
			case paragraphDepth > 0 && v.Name.Local == "br":
				paragraph.WriteByte('\n')
			}
		case xml.CharData:
			if paragraphDepth > 0 {
				paragraph.Write([]byte(v))
			}
		case xml.EndElement:
			switch {
			case depth == paragraphDepth:
				if text := strings.TrimSpace(paragraph.String()); text != "" {
					if !pageHasText {
						fmt.Fprintf(&out, "## 第 %d 页\n\n", page)
					}
					out.WriteString(text)
					out.WriteString("\n\n")
					pageHasText, hasText = true, true
				}
				paragraphDepth = 0
			case depth == contentDepth:
				contentDepth = 0
			case depth == 3 && inData:
				inData = false
			case depth == 2 && inSlide:
				inSlide = false
			}
			depth--
		}
	}
	if !hasText {
		return "", errors.New("no visible presentation text available for indexing")
	}
	return strings.TrimSpace(out.String()), nil
}
