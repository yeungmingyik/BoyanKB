package businessworkflow

import (
	"errors"
	"strings"
	"testing"
)

func TestProposalRejectsDuplicateSpreadsheetBudgetInputs(t *testing.T) {
	for _, headers := range [][]string{
		{"项目", "数量", "单价（元）"},
		{"项目", "数量（份）", "单价"},
		{"Item", "Qty", "Unit Price (CNY)"},
		{"Item", "QUANTITY", "unit_price"},
		{"Item", " quantity [units] ", "unit\tprice"},
	} {
		t.Run(strings.Join(headers, "/"), func(t *testing.T) {
			p := proposalForLimits()
			p.Format = "xlsx"
			p.Budget = &Budget{Items: []BudgetItem{{Label: "讲义", Quantity: 12, UnitPrice: 10}}}
			p.Document.Sections = append(p.Document.Sections, Section{Heading: "费用", Tables: []Table{
				{Headers: []string{"备注"}, Rows: [][]string{{"待询价"}}},
				{Headers: headers, Rows: [][]string{{"讲义", "12", "10"}}},
			}})
			err := ValidateProposal(p)
			if !errors.Is(err, ErrInvalid) || !strings.Contains(err.Error(), "document.sections[1].tables[1]") || !strings.Contains(err.Error(), "only in budget.items") {
				t.Fatalf("duplicate budget inputs must be rejected with correction guidance: %v", err)
			}
		})
	}
}

func TestProposalPreservesNonDuplicateBudgetTables(t *testing.T) {
	for _, tc := range []struct {
		name   string
		format string
		budget bool
		tables []Table
	}{
		{"quantity_only", "xlsx", true, []Table{{Headers: []string{"物料", "数量"}, Rows: [][]string{{"讲义", "12"}}}}},
		{"price_only", "xlsx", true, []Table{{Headers: []string{"单价", "询价状态"}, Rows: [][]string{{"待核实", "未询价"}}}}},
		{"price_metadata", "xlsx", true, []Table{{Headers: []string{"数量", "单价性质"}, Rows: [][]string{{"12", "待核实假设"}}}}},
		{"separate_tables", "xlsx", true, []Table{{Headers: []string{"数量"}, Rows: [][]string{{"12"}}}, {Headers: []string{"单价"}, Rows: [][]string{{"10"}}}}},
		{"no_budget", "xlsx", false, []Table{{Headers: []string{"数量", "单价"}, Rows: [][]string{{"12", "10"}}}}},
		{"word", "docx", true, []Table{{Headers: []string{"数量", "单价"}, Rows: [][]string{{"12", "10"}}}}},
		{"slides", "pptx", true, []Table{{Headers: []string{"数量", "单价"}, Rows: [][]string{{"12", "10"}}}}},
		{"markdown", "md", true, []Table{{Headers: []string{"数量", "单价"}, Rows: [][]string{{"12", "10"}}}}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			p := proposalForLimits()
			p.Format = tc.format
			if tc.budget {
				p.Budget = &Budget{Items: []BudgetItem{{Label: "讲义", Quantity: 12, UnitPrice: 10}}}
			}
			p.Document.Sections[0].Tables = tc.tables
			if err := ValidateProposal(p); err != nil {
				t.Fatalf("valid nonduplicate table rejected: %v", err)
			}
		})
	}
}
