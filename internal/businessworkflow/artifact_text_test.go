package businessworkflow

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func spreadsheetFixture(sheet string) map[string]string {
	return map[string]string{
		"xl/workbook.xml":            `<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="预算测算" sheetId="1" r:id="rId1"/></sheets></workbook>`,
		"xl/_rels/workbook.xml.rels": `<Relationships><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>`,
		"xl/sharedStrings.xml":       `<sst><si><t>博研文化</t></si><si><r><t>用户</t></r><r><t>预算假设</t></r></si><si><t>未被引用的字符串不得出现</t></si></sst>`,
		"xl/worksheets/sheet1.xml":   sheet,
	}
}

func TestSpreadsheetTextPreservesSharedStringsCoordinatesAndFormulaCache(t *testing.T) {
	entries := spreadsheetFixture(`<worksheet><sheetData>
<row r="3"><c r="E3"><f>SUM(E2:E2)</f><v>150</v></c></row>
<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="inlineStr"><is><r><t>课程</t></r><r><t>物料</t></r></is></c></row>
<row r="2"><c r="E2"><f>B2*D2</f><v>150</v></c><c r="B2"><v>3</v></c><c r="D2"><v>50</v></c><c r="F2" t="str"><f>IF(B2=3,&quot;正确&quot;,&quot;错误&quot;)</f><v>正确</v></c></row>
</sheetData></worksheet>`)
	text, err := artifactText(zipDocument(t, entries), "xlsx")
	if err != nil {
		t.Fatal(err)
	}
	for _, value := range []string{"[工作表: 预算测算]", "[第 1 行]", `A1 value="博研文化"`, `B1 value="用户预算假设"`, `C1 value="课程物料"`, `B2 value="3"`, `D2 value="50"`, `E2 formula="B2*D2" cached="150"`, `E3 formula="SUM(E2:E2)" cached="150"`, `cached="正确" type="str"`} {
		if !strings.Contains(text, value) {
			t.Fatalf("missing extraction %q: %s", value, text)
		}
	}
	if strings.Contains(text, "未被引用") || strings.Index(text, "[第 3 行]") < strings.Index(text, "[第 2 行]") || strings.Index(text, "E2 formula") < strings.Index(text, "D2 value") {
		t.Fatal("cell relation or ordering lost")
	}
}

func TestSpreadsheetTextRejectsInvalidReferencesAndMarksMissingCache(t *testing.T) {
	for _, cell := range []string{`<c r="A1" t="s"><v>99</v></c>`, `<c r="A1" t="s"><v>-1</v></c>`, `<c r="A+1"><v>0</v></c>`, `<c r="A1"><v>0</v></c><c r="A1"><v>1</v></c>`} {
		if _, err := artifactText(zipDocument(t, spreadsheetFixture("<worksheet><sheetData><row>"+cell+"</row></sheetData></worksheet>")), "xlsx"); err == nil {
			t.Fatal("invalid cell reference accepted")
		}
	}
	text, err := artifactText(zipDocument(t, spreadsheetFixture(`<worksheet><sheetData><row><c r="A1"><f>SUM(B1:C1)</f></c></row></sheetData></worksheet>`)), "xlsx")
	if err != nil || !strings.Contains(text, `A1 formula="SUM(B1:C1)" cached=<missing>`) {
		t.Fatalf("missing cache silently converted: %s %v", text, err)
	}
}

func TestPresentationTextKeepsNumericPageOrderAndLinkedNotes(t *testing.T) {
	entries := map[string]string{
		"ppt/presentation.xml":              `<presentation xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sldIdLst><sldId id="2" r:id="slide2"/><sldId id="10" r:id="slide10"/></sldIdLst></presentation>`,
		"ppt/_rels/presentation.xml.rels":   `<Relationships><Relationship Id="slide2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide2.xml"/><Relationship Id="slide10" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide10.xml"/></Relationships>`,
		"ppt/slides/slide10.xml":            `<slide><p><r><t>第十页</t></r></p></slide>`,
		"ppt/slides/slide2.xml":             `<slide><p><r><t>第二</t></r><r><t>页正文</t></r></p></slide>`,
		"ppt/slides/_rels/slide2.xml.rels":  `<Relationships><Relationship Id="rNotes" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide17.xml"/></Relationships>`,
		"ppt/notesSlides/notesSlide17.xml":  `<notes><p><r><t>总部课程 分块 13a5da36-7d2d-4481-86ec-fc57c4b0ac50</t></r></p></notes>`,
		"ppt/notesMasters/notesMaster1.xml": `<notes><p><t>不能混入母版占位说明</t></p></notes>`,
	}
	text, err := artifactText(zipDocument(t, entries), "pptx")
	if err != nil {
		t.Fatal(err)
	}
	for _, value := range []string{"[第 1 页 正文]\n第二页正文", "[第 1 页 备注]\n总部课程 分块 13a5da36-7d2d-4481-86ec-fc57c4b0ac50", "[第 2 页 正文]\n第十页"} {
		if !strings.Contains(text, value) {
			t.Fatalf("missing page/notes relation: %q in %s", value, text)
		}
	}
	if strings.Index(text, "第二页正文") > strings.Index(text, "第十页") || strings.Contains(text, "母版") {
		t.Fatal("invalid page order or irrelevant master text")
	}
	entries["ppt/slides/_rels/slide2.xml.rels"] = `<Relationships><Relationship Id="rNotes" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../../../outside.xml"/></Relationships>`
	if _, err := artifactText(zipDocument(t, entries), "pptx"); err == nil {
		t.Fatal("notes traversal accepted")
	}
}

func TestWordTextIncludesOnlyVisibleDocumentHeaderAndFooter(t *testing.T) {
	entries := map[string]string{
		"word/document.xml": `<document><p><r><t>博研</t></r><r><t>文化</t></r><r><instrText>INVISIBLE_FIELD_CODE</instrText></r></p></document>`,
		"word/header1.xml":  `<hdr><p><t>页眉品牌</t></p></hdr>`,
		"word/footer1.xml":  `<ftr><p><t>页脚来源</t></p></ftr>`,
		"word/styles.xml":   `<styles><style><name val="Normal"/><t>HIDDEN_STYLE_TEXT</t></style></styles>`,
		"word/settings.xml": `<settings><t>HIDDEN_SETTING</t></settings>`,
	}
	text, err := artifactText(zipDocument(t, entries), "docx")
	if err != nil || !strings.Contains(text, "博研文化\n") || !strings.Contains(text, "页眉品牌") || !strings.Contains(text, "页脚来源") || strings.Contains(text, "HIDDEN_") || strings.Contains(text, "INVISIBLE_") {
		t.Fatalf("incorrect visible document text: %s %v", text, err)
	}
}

func TestXLSXProposalRejectsModelFormulasBeforeConfirmation(t *testing.T) {
	_, _, p := fixture(t)
	p.Format = "xlsx"
	for _, value := range []string{"=B2*D2", "'=B2*D2", "  '  =SUM(A1:A3)", "''=SUM()"} {
		p.Document.Sections[0].Tables = []Table{{Headers: []string{"预算"}, Rows: [][]string{{value}}}}
		err := ValidateProposal(p)
		if !errors.Is(err, ErrInvalid) || !strings.Contains(err.Error(), "budget.items") {
			t.Fatalf("formula did not produce actionable error: %q %v", value, err)
		}
	}
	p.Document.Sections[0].Tables = []Table{{Headers: []string{"计划"}, Rows: [][]string{{"-10 至 0 度"}, {"普通说明"}}}}
	if err := ValidateProposal(p); err != nil {
		t.Fatal(err)
	}
	p.Format = "docx"
	p.Document.Sections[0].Tables = []Table{{Headers: []string{"公式说明"}, Rows: [][]string{{"=B2*D2"}}}}
	if err := ValidateProposal(p); err != nil {
		t.Fatal(err)
	}
}

func TestGeneratedOfficeArtifactText(t *testing.T) {
	directory := os.Getenv("BOYAN_RENDERED_FIXTURES_DIR")
	if directory == "" {
		t.Skip("BOYAN_RENDERED_FIXTURES_DIR is required")
	}
	for _, tc := range []struct {
		name, format string
		required     []string
	}{
		{"operations-rounding.xlsx", "xlsx", []string{"[工作表:", "formula=", "cached=", "博研文化"}},
		{"teaching-long-content.pptx", "pptx", []string{"[第 1 页 正文]", "页 备注]", "分块", "博研文化"}},
		{"teaching.docx", "docx", []string{"博研文化"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			data, err := os.ReadFile(filepath.Join(directory, tc.name))
			if err != nil {
				t.Fatal(err)
			}
			text, err := artifactText(data, tc.format)
			if err != nil {
				t.Fatal(err)
			}
			for _, value := range tc.required {
				if !strings.Contains(text, value) {
					t.Fatalf("rendered artifact missing %q", value)
				}
			}
			if strings.Contains(text, "cached=<missing>") || strings.Contains(text, `type="e"`) {
				t.Fatal("invalid rendered formula cache")
			}
			t.Logf("format=%s extracted_bytes=%d", tc.format, len(text))
		})
	}
}
