package businessworkflow

import (
	"fmt"
	"strings"
	"unicode"
)

func validateBudgetTables(p Proposal) error {
	if p.Format != "xlsx" || p.Budget == nil {
		return nil
	}
	for sectionIndex, section := range p.Document.Sections {
		for tableIndex, table := range section.Tables {
			quantity, unitPrice := false, false
			for _, header := range table.Headers {
				header = strings.ToLower(strings.TrimSpace(header))
				if index := strings.IndexAny(header, "(（[【"); index >= 0 {
					header = header[:index]
				}
				header = strings.Map(func(r rune) rune {
					if unicode.IsSpace(r) || r == '_' || r == '-' {
						return -1
					}
					return r
				}, header)
				switch header {
				case "数量", "qty", "quantity":
					quantity = true
				case "单价", "unitprice":
					unitPrice = true
				}
			}
			if quantity && unitPrice {
				return fmt.Errorf("%w: document.sections[%d].tables[%d] duplicates budget inputs; put quantity and unit_price details only in budget.items and remove this table", ErrInvalid, sectionIndex, tableIndex)
			}
		}
	}
	return nil
}
