package businessworkflow

import (
	"errors"
	"math"
	"strings"
	"testing"
)

func proposalForLimits() Proposal {
	return Proposal{Scene: "faq-guide", Format: "md", OutlineMarkdown: "# 博研文化资料问答手册", Requirements: map[string]string{"audience": "伙伴", "topic": "业务"}, Document: Document{Title: "业务", Sections: []Section{{Heading: "内容", Body: "已核实内容"}}}, Sources: []SourceRequest{{ChunkID: "source"}}}
}

func TestRendererTextLimitsUseUnicodeCharacters(t *testing.T) {
	for _, format := range []string{"docx", "pptx", "xlsx", "md"} {
		headingLimit := 100
		titleLimit := 100
		if format == "pptx" {
			headingLimit = 40
			titleLimit = 36
		}
		for _, tc := range []struct {
			name  string
			limit int
			set   func(*Proposal, string)
		}{
			{"document.title", titleLimit, func(p *Proposal, v string) { p.Document.Title = v }},
			{"heading", headingLimit, func(p *Proposal, v string) { p.Document.Sections[0].Heading = v }},
			{"body", 12000, func(p *Proposal, v string) { p.Document.Sections[0].Body = v }},
			{"notes", 5000, func(p *Proposal, v string) { p.Document.Sections[0].Notes = v }},
			{"bullets", 2000, func(p *Proposal, v string) { p.Document.Sections[0].Bullets = []string{v} }},
			{"headers", 100, func(p *Proposal, v string) {
				p.Document.Sections[0].Tables = []Table{{Headers: []string{v}, Rows: [][]string{}}}
			}},
			{"rows", 1000, func(p *Proposal, v string) {
				p.Document.Sections[0].Tables = []Table{{Headers: []string{"值"}, Rows: [][]string{{v}}}}
			}},
			{"label", 150, func(p *Proposal, v string) {
				p.Budget = &Budget{Items: []BudgetItem{{Label: v, Quantity: 1, UnitPrice: 1}}}
			}},
			{"chunk_id", 100, func(p *Proposal, v string) { p.Sources[0].ChunkID = v }},
		} {
			t.Run(format+"/"+tc.name, func(t *testing.T) {
				p := proposalForLimits()
				p.Format = format
				tc.set(&p, strings.Repeat("课", tc.limit-1)+"😀")
				if err := ValidateProposal(p); err != nil {
					t.Fatalf("valid Unicode boundary rejected: %v", err)
				}
				tc.set(&p, strings.Repeat("课", tc.limit)+"😀")
				err := ValidateProposal(p)
				if !errors.Is(err, ErrInvalid) || !strings.Contains(err.Error(), tc.name) || !strings.Contains(err.Error(), "Unicode characters") {
					t.Fatalf("missing actionable overflow: %v", err)
				}
			})
		}
	}
}

func TestRendererCollectionLimitsFailBeforeConfirmation(t *testing.T) {
	for _, tc := range []struct {
		name  string
		limit int
		set   func(*Proposal, int)
	}{
		{"sections", 40, func(p *Proposal, count int) {
			p.Document.Sections = make([]Section, count)
			for i := range p.Document.Sections {
				p.Document.Sections[i] = Section{Heading: "节"}
			}
		}},
		{"bullets", 40, func(p *Proposal, count int) {
			p.Document.Sections[0].Bullets = make([]string, count)
			for i := range p.Document.Sections[0].Bullets {
				p.Document.Sections[0].Bullets[i] = "点"
			}
		}},
		{"tables", 4, func(p *Proposal, count int) {
			p.Document.Sections[0].Tables = make([]Table, count)
			for i := range p.Document.Sections[0].Tables {
				p.Document.Sections[0].Tables[i] = Table{Headers: []string{"值"}, Rows: [][]string{}}
			}
		}},
		{"headers", 8, func(p *Proposal, count int) {
			headers := make([]string, count)
			for i := range headers {
				headers[i] = "值"
			}
			p.Document.Sections[0].Tables = []Table{{Headers: headers, Rows: [][]string{}}}
		}},
		{"rows", 150, func(p *Proposal, count int) {
			rows := make([][]string, count)
			for i := range rows {
				rows[i] = []string{"值"}
			}
			p.Document.Sections[0].Tables = []Table{{Headers: []string{"列"}, Rows: rows}}
		}},
		{"budget.items", 150, func(p *Proposal, count int) {
			p.Budget = &Budget{Items: make([]BudgetItem, count)}
			for i := range p.Budget.Items {
				p.Budget.Items[i] = BudgetItem{Label: "物料", Quantity: 1, UnitPrice: 1}
			}
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			p := proposalForLimits()
			tc.set(&p, tc.limit)
			if err := ValidateProposal(p); err != nil {
				t.Fatal(err)
			}
			tc.set(&p, tc.limit+1)
			if err := ValidateProposal(p); !errors.Is(err, ErrInvalid) || !strings.Contains(err.Error(), tc.name) {
				t.Fatalf("limit not explained: %v", err)
			}
		})
	}
	svc, ctx, p := fixture(t)
	p.Document.Title = strings.Repeat("a", 101)
	if _, err := svc.Propose(ctx, "session", p); !errors.Is(err, ErrInvalid) {
		t.Fatal("oversized proposal persisted")
	}
	var count int64
	if err := svc.DB.Model(&Job{}).Count(&count).Error; err != nil || count != 0 {
		t.Fatal("unrenderable confirmation job exists")
	}
}

func TestRendererTextAndBudgetInvalidInputs(t *testing.T) {
	for _, text := range []string{" ", "内容\x00", "内容\v", "内容\x1f", "{{title}}", "{{a\nb}}", string([]byte{0xff})} {
		p := proposalForLimits()
		p.Document.Title = text
		if err := ValidateProposal(p); !errors.Is(err, ErrInvalid) || !strings.Contains(err.Error(), "document.title") {
			t.Fatalf("invalid text accepted: %v", err)
		}
	}
	p := proposalForLimits()
	p.Document.Sections[0].Body = "内容\n\r\t允许换行"
	p.Budget = &Budget{Items: []BudgetItem{{Label: "物料", Quantity: 100000000, UnitPrice: 100000000}}}
	if err := ValidateProposal(p); err != nil {
		t.Fatal(err)
	}
	for _, value := range []float64{-1, 100000001, math.NaN(), math.Inf(1)} {
		for _, field := range []string{"quantity", "unit_price"} {
			p.Budget.Items[0].Quantity, p.Budget.Items[0].UnitPrice = 0, 0
			if field == "quantity" {
				p.Budget.Items[0].Quantity = value
			} else {
				p.Budget.Items[0].UnitPrice = value
			}
			if err := ValidateProposal(p); !errors.Is(err, ErrInvalid) || !strings.Contains(err.Error(), field) {
				t.Fatalf("invalid budget accepted: %v", err)
			}
		}
	}
	p = proposalForLimits()
	p.Document.Sections[0].Tables = []Table{{Headers: []string{"值"}}}
	if err := ValidateProposal(p); !errors.Is(err, ErrInvalid) || !strings.Contains(err.Error(), "must be an array") {
		t.Fatal("null rows accepted")
	}
}

func TestSourceRendererTextLimitsFailBeforeConfirmation(t *testing.T) {
	svc, ctx, p := fixture(t)
	if err := svc.DB.Exec("UPDATE chunks SET content = ? WHERE id = ?", strings.Repeat("课", 24000), "chunk").Error; err != nil {
		t.Fatal(err)
	}
	if _, err := svc.Propose(ctx, "session", p); err != nil {
		t.Fatalf("valid Unicode source rejected: %v", err)
	}
	if err := svc.DB.Exec("UPDATE knowledges SET title = ? WHERE id = ?", strings.Repeat("源", 301), "knowledge").Error; err != nil {
		t.Fatal(err)
	}
	if _, err := svc.Propose(ctx, "session", p); !errors.Is(err, ErrInvalid) || !strings.Contains(err.Error(), "source.title") {
		t.Fatal("oversized source title accepted")
	}
}
