package businessworkflow

import (
	"fmt"
	"math"
	"regexp"
	"strings"
	"unicode/utf8"
)

var rendererPlaceholder = regexp.MustCompile(`\{\{[^}]*\}\}`)

func validateRendererText(field, value string, limit int, required bool) error {
	if !utf8.ValidString(value) {
		return fmt.Errorf("%w: %s must contain valid Unicode text", ErrInvalid, field)
	}
	if required && strings.TrimSpace(value) == "" {
		return fmt.Errorf("%w: %s is required and cannot be blank", ErrInvalid, field)
	}
	if utf8.RuneCountInString(value) > limit {
		return fmt.Errorf("%w: shorten %s to at most %d Unicode characters", ErrInvalid, field, limit)
	}
	for _, r := range value {
		if r <= 8 || r == 11 || r == 12 || r >= 14 && r <= 31 {
			return fmt.Errorf("%w: remove control characters from %s", ErrInvalid, field)
		}
	}
	if rendererPlaceholder.MatchString(value) {
		return fmt.Errorf("%w: replace template placeholders in %s with final text", ErrInvalid, field)
	}
	return nil
}

func validateRendererDocument(document Document, format string) error {
	titleLimit := 100
	if format == "pptx" {
		titleLimit = 36
	}
	if err := validateRendererText("document.title", document.Title, titleLimit, true); err != nil {
		return err
	}
	if len(document.Sections) < 1 || len(document.Sections) > 40 {
		return fmt.Errorf("%w: document.sections must contain 1 to 40 sections", ErrInvalid)
	}
	for index, section := range document.Sections {
		prefix := fmt.Sprintf("document.sections[%d]", index)
		headingLimit := 100
		if format == "pptx" {
			headingLimit = 40
		}
		for _, field := range []struct {
			name, value string
			limit       int
			required    bool
		}{
			{prefix + ".heading", section.Heading, headingLimit, true},
			{prefix + ".body", section.Body, 12000, false},
			{prefix + ".notes", section.Notes, 5000, false},
		} {
			if err := validateRendererText(field.name, field.value, field.limit, field.required); err != nil {
				return err
			}
		}
		if len(section.Bullets) > 40 {
			return fmt.Errorf("%w: %s.bullets must contain at most 40 items", ErrInvalid, prefix)
		}
		for i, value := range section.Bullets {
			if err := validateRendererText(fmt.Sprintf("%s.bullets[%d]", prefix, i), value, 2000, true); err != nil {
				return err
			}
		}
		if len(section.Tables) > 4 {
			return fmt.Errorf("%w: %s.tables must contain at most 4 tables", ErrInvalid, prefix)
		}
		for tableIndex, table := range section.Tables {
			tablePrefix := fmt.Sprintf("%s.tables[%d]", prefix, tableIndex)
			if len(table.Headers) < 1 || len(table.Headers) > 8 {
				return fmt.Errorf("%w: %s.headers must contain 1 to 8 columns", ErrInvalid, tablePrefix)
			}
			if len(table.Rows) > 150 {
				return fmt.Errorf("%w: %s.rows must contain at most 150 rows", ErrInvalid, tablePrefix)
			}
			if table.Rows == nil {
				return fmt.Errorf("%w: %s.rows must be an array; use [] for an empty table", ErrInvalid, tablePrefix)
			}
			for i, value := range table.Headers {
				if err := validateRendererText(fmt.Sprintf("%s.headers[%d]", tablePrefix, i), value, 100, true); err != nil {
					return err
				}
				if format == "xlsx" && spreadsheetFormulaText(value) {
					return ErrTableFormula
				}
			}
			for rowIndex, row := range table.Rows {
				if len(row) != len(table.Headers) {
					return fmt.Errorf("%w: %s.rows[%d] must match the %d header columns", ErrInvalid, tablePrefix, rowIndex, len(table.Headers))
				}
				for column, value := range row {
					if err := validateRendererText(fmt.Sprintf("%s.rows[%d][%d]", tablePrefix, rowIndex, column), value, 1000, false); err != nil {
						return err
					}
					if format == "xlsx" && spreadsheetFormulaText(value) {
						return ErrTableFormula
					}
				}
			}
		}
	}
	return nil
}

func validateRendererBudget(budget *Budget) error {
	if budget == nil {
		return nil
	}
	if len(budget.Items) < 1 || len(budget.Items) > 150 {
		return fmt.Errorf("%w: budget.items must contain 1 to 150 items", ErrInvalid)
	}
	for index, item := range budget.Items {
		prefix := fmt.Sprintf("budget.items[%d]", index)
		if err := validateRendererText(prefix+".label", item.Label, 150, true); err != nil {
			return err
		}
		for _, field := range []struct {
			name  string
			value float64
		}{{"quantity", item.Quantity}, {"unit_price", item.UnitPrice}} {
			if math.IsNaN(field.value) || math.IsInf(field.value, 0) || field.value < 0 || field.value > 100000000 {
				return fmt.Errorf("%w: %s.%s must be a finite number between 0 and 100000000", ErrInvalid, prefix, field.name)
			}
		}
	}
	return nil
}
