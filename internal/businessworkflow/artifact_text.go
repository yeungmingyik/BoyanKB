package businessworkflow

import (
	"archive/zip"
	"bytes"
	"encoding/xml"
	"fmt"
	"io"
	"path"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

func artifactText(data []byte, format string) (string, error) {
	if format == "md" {
		if !utf8.Valid(data) || len(data) > 512*1024 {
			return "", ErrInvalid
		}
		return string(data), nil
	}
	required := map[string]string{"docx": "word/document.xml", "pptx": "ppt/presentation.xml", "xlsx": "xl/workbook.xml"}[format]
	if required == "" {
		return "", ErrInvalid
	}
	parts, err := officeParts(data)
	if err != nil || parts[required] == nil {
		return "", ErrInvalid
	}
	var text string
	switch format {
	case "docx":
		text, err = wordText(parts)
	case "pptx":
		text, err = presentationText(parts)
	case "xlsx":
		text, err = spreadsheetText(parts)
	}
	if err != nil || strings.TrimSpace(text) == "" || len(text) > 512*1024 {
		return "", ErrInvalid
	}
	return text, nil
}

func officeParts(data []byte) (map[string][]byte, error) {
	z, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil || len(z.File) > 2000 {
		return nil, ErrInvalid
	}
	parts := map[string][]byte{}
	seen := map[string]bool{}
	var total uint64
	for _, file := range z.File {
		lower := strings.ToLower(file.Name)
		if seen[file.Name] || strings.Contains(file.Name, "..") || strings.Contains(file.Name, "\\") || strings.HasPrefix(file.Name, "/") || strings.HasSuffix(lower, "vbaproject.bin") || strings.Contains(lower, "externallinks/") {
			return nil, ErrInvalid
		}
		seen[file.Name] = true
		total += file.UncompressedSize64
		if total > 64*1024*1024 || file.UncompressedSize64 > 16*1024*1024 {
			return nil, ErrInvalid
		}
		if !strings.HasSuffix(file.Name, ".xml") && !strings.HasSuffix(file.Name, ".rels") {
			continue
		}
		r, err := file.Open()
		if err != nil {
			return nil, ErrInvalid
		}
		body, readErr := io.ReadAll(io.LimitReader(r, 16*1024*1024+1))
		r.Close()
		if readErr != nil || len(body) > 16*1024*1024 {
			return nil, ErrInvalid
		}
		decoder := xml.NewDecoder(bytes.NewReader(body))
		for {
			token, err := decoder.Token()
			if err == io.EOF {
				break
			}
			if err != nil {
				return nil, ErrInvalid
			}
			if element, ok := token.(xml.StartElement); ok {
				for _, attr := range element.Attr {
					if attr.Name.Local == "TargetMode" && strings.EqualFold(attr.Value, "External") {
						return nil, ErrInvalid
					}
				}
			}
		}
		parts[file.Name] = body
	}
	return parts, nil
}

func visibleOfficeText(data []byte) (string, error) {
	decoder := xml.NewDecoder(bytes.NewReader(data))
	var result strings.Builder
	for {
		token, err := decoder.Token()
		if err == io.EOF {
			break
		}
		if err != nil {
			return "", ErrInvalid
		}
		switch element := token.(type) {
		case xml.StartElement:
			switch element.Name.Local {
			case "t":
				var value string
				if decoder.DecodeElement(&value, &element) != nil {
					return "", ErrInvalid
				}
				result.WriteString(value)
			case "tab":
				result.WriteByte('\t')
			case "br":
				result.WriteByte('\n')
			}
		case xml.EndElement:
			if element.Name.Local == "p" {
				result.WriteByte('\n')
			}
			if element.Name.Local == "tc" {
				result.WriteByte('\t')
			}
		}
	}
	return result.String(), nil
}

func numberedOfficeParts(parts map[string][]byte, prefix string) []string {
	var keys []string
	for name := range parts {
		if strings.HasPrefix(name, prefix) && strings.HasSuffix(name, ".xml") {
			number := strings.TrimSuffix(strings.TrimPrefix(name, prefix), ".xml")
			if n, err := strconv.Atoi(number); err == nil && n > 0 {
				keys = append(keys, name)
			}
		}
	}
	sort.Slice(keys, func(i, j int) bool {
		a, _ := strconv.Atoi(strings.TrimSuffix(strings.TrimPrefix(keys[i], prefix), ".xml"))
		b, _ := strconv.Atoi(strings.TrimSuffix(strings.TrimPrefix(keys[j], prefix), ".xml"))
		return a < b
	})
	return keys
}

func wordText(parts map[string][]byte) (string, error) {
	result, err := visibleOfficeText(parts["word/document.xml"])
	if err != nil {
		return "", err
	}
	for _, kind := range []string{"header", "footer"} {
		for _, name := range numberedOfficeParts(parts, "word/"+kind) {
			text, err := visibleOfficeText(parts[name])
			if err != nil {
				return "", err
			}
			if strings.TrimSpace(text) != "" {
				result += "\n[" + name + "]\n" + text
			}
		}
	}
	return result, nil
}

type officeRelationship struct {
	ID     string `xml:"Id,attr"`
	Type   string `xml:"Type,attr"`
	Target string `xml:"Target,attr"`
}

func relationshipsFor(parts map[string][]byte, source string) (map[string]officeRelationship, error) {
	name := path.Join(path.Dir(source), "_rels", path.Base(source)+".rels")
	result := map[string]officeRelationship{}
	if parts[name] == nil {
		return result, nil
	}
	var relationships struct {
		Items []officeRelationship `xml:"Relationship"`
	}
	if xml.Unmarshal(parts[name], &relationships) != nil {
		return nil, ErrInvalid
	}
	for _, relationship := range relationships.Items {
		if _, duplicate := result[relationship.ID]; duplicate || relationship.ID == "" {
			return nil, ErrInvalid
		}
		result[relationship.ID] = relationship
	}
	return result, nil
}

func relationshipPart(parts map[string][]byte, source, target string) (string, error) {
	if target == "" || strings.ContainsAny(target, "\\:?#") {
		return "", ErrInvalid
	}
	name := path.Clean(path.Join(path.Dir(source), target))
	if strings.HasPrefix(target, "/") {
		name = path.Clean(strings.TrimPrefix(target, "/"))
	}
	if strings.HasPrefix(name, "../") || parts[name] == nil {
		return "", ErrInvalid
	}
	return name, nil
}

func presentationText(parts map[string][]byte) (string, error) {
	var presentation struct {
		Slides []struct {
			ID string `xml:"http://schemas.openxmlformats.org/officeDocument/2006/relationships id,attr"`
		} `xml:"sldIdLst>sldId"`
	}
	if xml.Unmarshal(parts["ppt/presentation.xml"], &presentation) != nil || len(presentation.Slides) == 0 {
		return "", ErrInvalid
	}
	presentationRelationships, err := relationshipsFor(parts, "ppt/presentation.xml")
	if err != nil {
		return "", err
	}
	var result strings.Builder
	seen := map[string]bool{}
	for index, slide := range presentation.Slides {
		relationship, ok := presentationRelationships[slide.ID]
		if !ok || !strings.HasSuffix(relationship.Type, "/slide") {
			return "", ErrInvalid
		}
		name, err := relationshipPart(parts, "ppt/presentation.xml", relationship.Target)
		if err != nil || !strings.HasPrefix(name, "ppt/slides/") || seen[name] {
			return "", ErrInvalid
		}
		seen[name] = true
		page := strconv.Itoa(index + 1)
		body, err := visibleOfficeText(parts[name])
		if err != nil {
			return "", err
		}
		fmt.Fprintf(&result, "[第 %s 页 正文]\n%s\n", page, body)
		relationships, err := relationshipsFor(parts, name)
		if err != nil {
			return "", err
		}
		var notes []string
		for _, relationship := range relationships {
			if !strings.HasSuffix(relationship.Type, "/notesSlide") {
				continue
			}
			target, err := relationshipPart(parts, name, relationship.Target)
			if err != nil || !strings.HasPrefix(target, "ppt/notesSlides/") {
				return "", ErrInvalid
			}
			notes = append(notes, target)
		}
		sort.Strings(notes)
		for _, target := range notes {
			body, err := visibleOfficeText(parts[target])
			if err != nil {
				return "", err
			}
			fmt.Fprintf(&result, "[第 %s 页 备注]\n%s\n", page, body)
		}
	}
	return result.String(), nil
}

type spreadsheetRichText struct {
	Text string `xml:"t"`
	Runs []struct {
		Text string `xml:"t"`
	} `xml:"r"`
}

func (r spreadsheetRichText) String() string {
	var text strings.Builder
	text.WriteString(r.Text)
	for _, run := range r.Runs {
		text.WriteString(run.Text)
	}
	return text.String()
}

type spreadsheetCell struct {
	Address string              `xml:"r,attr"`
	Type    string              `xml:"t,attr"`
	Value   *string             `xml:"v"`
	Inline  spreadsheetRichText `xml:"is"`
	Formula *struct {
		Text        string `xml:",chardata"`
		Type        string `xml:"t,attr"`
		SharedIndex string `xml:"si,attr"`
		Range       string `xml:"ref,attr"`
	} `xml:"f"`
}

func spreadsheetAddress(address string) (int, int, error) {
	column, i := 0, 0
	for i < len(address) && address[i] >= 'A' && address[i] <= 'Z' {
		column = column*26 + int(address[i]-'A') + 1
		i++
		if column > 16384 {
			return 0, 0, ErrInvalid
		}
	}
	if i == 0 || i == len(address) || address[i] == '0' {
		return 0, 0, ErrInvalid
	}
	for _, digit := range address[i:] {
		if digit < '0' || digit > '9' {
			return 0, 0, ErrInvalid
		}
	}
	row, err := strconv.Atoi(address[i:])
	if err != nil || row < 1 || row > 1048576 {
		return 0, 0, ErrInvalid
	}
	return row, column, nil
}

func spreadsheetText(parts map[string][]byte) (string, error) {
	var shared struct {
		Items []spreadsheetRichText `xml:"si"`
	}
	if data := parts["xl/sharedStrings.xml"]; data != nil && xml.Unmarshal(data, &shared) != nil {
		return "", ErrInvalid
	}
	var workbook struct {
		Sheets []struct {
			Name string `xml:"name,attr"`
			ID   string `xml:"http://schemas.openxmlformats.org/officeDocument/2006/relationships id,attr"`
		} `xml:"sheets>sheet"`
	}
	if xml.Unmarshal(parts["xl/workbook.xml"], &workbook) != nil || len(workbook.Sheets) == 0 {
		return "", ErrInvalid
	}
	relationships, err := relationshipsFor(parts, "xl/workbook.xml")
	if err != nil {
		return "", err
	}
	var result strings.Builder
	for _, sheet := range workbook.Sheets {
		relationship, ok := relationships[sheet.ID]
		if !ok || !strings.HasSuffix(relationship.Type, "/worksheet") {
			return "", ErrInvalid
		}
		name, err := relationshipPart(parts, "xl/workbook.xml", relationship.Target)
		if err != nil || !strings.HasPrefix(name, "xl/worksheets/") {
			return "", ErrInvalid
		}
		var worksheet struct {
			Rows []struct {
				Cells []spreadsheetCell `xml:"c"`
			} `xml:"sheetData>row"`
		}
		if xml.Unmarshal(parts[name], &worksheet) != nil {
			return "", ErrInvalid
		}
		fmt.Fprintf(&result, "[工作表: %s]\n", sheet.Name)
		var cells []spreadsheetCell
		seen := map[string]bool{}
		for _, row := range worksheet.Rows {
			for _, cell := range row.Cells {
				if _, _, err := spreadsheetAddress(cell.Address); err != nil || seen[cell.Address] {
					return "", ErrInvalid
				}
				seen[cell.Address] = true
				cells = append(cells, cell)
			}
		}
		sort.Slice(cells, func(i, j int) bool {
			ri, ci, _ := spreadsheetAddress(cells[i].Address)
			rj, cj, _ := spreadsheetAddress(cells[j].Address)
			return ri < rj || ri == rj && ci < cj
		})
		previousRow := 0
		for _, cell := range cells {
			row, _, _ := spreadsheetAddress(cell.Address)
			if row != previousRow {
				fmt.Fprintf(&result, "[第 %d 行]\n", row)
				previousRow = row
			}
			value := ""
			if cell.Value != nil {
				value = *cell.Value
			}
			switch cell.Type {
			case "s":
				index, err := strconv.Atoi(value)
				if err != nil || index < 0 || index >= len(shared.Items) {
					return "", ErrInvalid
				}
				value = shared.Items[index].String()
			case "inlineStr":
				value = cell.Inline.String()
			}
			if cell.Formula != nil {
				fmt.Fprintf(&result, "%s formula=%q", cell.Address, cell.Formula.Text)
				if cell.Formula.Type != "" {
					fmt.Fprintf(&result, " formula_type=%q shared_index=%q range=%q", cell.Formula.Type, cell.Formula.SharedIndex, cell.Formula.Range)
				}
				if cell.Value == nil {
					result.WriteString(" cached=<missing>")
				} else {
					fmt.Fprintf(&result, " cached=%q", value)
				}
				fmt.Fprintf(&result, " type=%q\n", cell.Type)
			} else {
				fmt.Fprintf(&result, "%s value=%q type=%q\n", cell.Address, value, cell.Type)
			}
		}
	}
	return result.String(), nil
}
