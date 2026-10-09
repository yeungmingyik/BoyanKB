package businessworkflow

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"
)

var ErrDenied = errors.New("document_access_denied")
var ErrInvalid = errors.New("document_request_invalid")
var ErrTableFormula = fmt.Errorf("%w: xlsx table formulas are not supported; remove formula cells and provide numeric budget.items label, quantity, unit_price; the renderer calculates the budget automatically", ErrInvalid)
var ErrConflict = errors.New("document_state_conflict")
var ErrUnavailable = errors.New("document_service_unavailable")

const AwaitingConfirmation = "awaiting_confirmation"
const Approved = "approved"
const Generating = "generating"
const Reviewing = "reviewing"
const Published = "published"
const Blocked = "blocked"
const Failed = "failed"
const Superseded = "superseded"

type Table struct {
	Headers []string   `json:"headers"`
	Rows    [][]string `json:"rows"`
}

type Section struct {
	Heading string   `json:"heading"`
	Body    string   `json:"body"`
	Bullets []string `json:"bullets,omitempty"`
	Tables  []Table  `json:"tables,omitempty"`
	Notes   string   `json:"notes,omitempty"`
}

type Document struct {
	Title    string    `json:"title"`
	Sections []Section `json:"sections"`
}

type Source struct {
	Authority       string `json:"authority"`
	ID              string `json:"id"`
	ChunkID         string `json:"chunk_id"`
	KnowledgeID     string `json:"knowledge_id"`
	KnowledgeBaseID string `json:"knowledge_base_id"`
	Title           string `json:"title"`
	Excerpt         string `json:"excerpt"`
	SHA256          string `json:"sha256"`
}

type SourceRequest struct {
	ChunkID string `json:"chunk_id"`
}

type BudgetItem struct {
	Label     string  `json:"label"`
	Quantity  float64 `json:"quantity"`
	UnitPrice float64 `json:"unit_price"`
}

func (b *BudgetItem) UnmarshalJSON(data []byte) error {
	var input struct {
		Label     string   `json:"label"`
		Quantity  *float64 `json:"quantity"`
		UnitPrice *float64 `json:"unit_price"`
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&input) != nil || input.Quantity == nil || input.UnitPrice == nil {
		return ErrInvalid
	}
	var extra interface{}
	if decoder.Decode(&extra) != io.EOF {
		return ErrInvalid
	}
	b.Label, b.Quantity, b.UnitPrice = input.Label, *input.Quantity, *input.UnitPrice
	return nil
}

type Budget struct {
	Items []BudgetItem `json:"items"`
}

type Proposal struct {
	Scene           string            `json:"scene"`
	Format          string            `json:"format"`
	OutlineMarkdown string            `json:"outline_markdown"`
	Requirements    map[string]string `json:"requirements"`
	Document        Document          `json:"document"`
	Sources         []SourceRequest   `json:"sources"`
	Budget          *Budget           `json:"budget,omitempty"`
}

type Payload struct {
	Proposal
	VerifiedSources []Source          `json:"verified_sources"`
	TemplateVersion string            `json:"template_version"`
	SkillHashes     map[string]string `json:"skill_hashes"`
}

type Issue struct {
	Location   string `json:"location"`
	Problem    string `json:"problem"`
	Severity   string `json:"severity"`
	Suggestion string `json:"suggestion"`
}

type Review struct {
	Passed bool    `json:"passed"`
	Issues []Issue `json:"issues"`
}

type Job struct {
	ID              string     `gorm:"primaryKey;type:varchar(36)" json:"id"`
	TenantID        uint64     `gorm:"index;not null" json:"-"`
	UserID          string     `gorm:"type:varchar(512);not null" json:"-"`
	SessionID       string     `gorm:"index;type:varchar(36);not null" json:"session_id"`
	State           string     `gorm:"type:varchar(32);not null" json:"state"`
	Scene           string     `gorm:"type:varchar(40);not null" json:"scene"`
	OutlineHash     string     `gorm:"type:varchar(64);not null" json:"outline_hash"`
	OutlineMarkdown string     `gorm:"type:text;not null" json:"outline_markdown"`
	Format          string     `gorm:"type:varchar(8);not null" json:"format"`
	Payload         []byte     `gorm:"type:bytea;not null" json:"-"`
	ReviewJSON      []byte     `gorm:"column:review_json;type:bytea" json:"-"`
	Artifact        []byte     `gorm:"type:bytea" json:"-"`
	ArtifactSHA256  string     `gorm:"type:varchar(64)" json:"artifact_sha256,omitempty"`
	Filename        string     `gorm:"type:varchar(255)" json:"filename,omitempty"`
	MimeType        string     `gorm:"type:varchar(128)" json:"mime_type,omitempty"`
	FailureCode     string     `gorm:"type:varchar(64)" json:"failure_code,omitempty"`
	ConfirmedAt     *time.Time `json:"confirmed_at,omitempty"`
	CreatedAt       time.Time  `json:"created_at"`
	UpdatedAt       time.Time  `json:"updated_at"`
}

func (Job) TableName() string { return "boyan_document_jobs" }

func digest(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

var RequiredFields = map[string][]string{
	"courseware":           {"audience", "duration", "objectives", "localization"},
	"event-plan":           {"city", "event_type", "purpose", "audience", "headcount", "budget", "schedule"},
	"local-promotion-plan": {"city", "business", "target_customers", "goal", "budget_range", "period"},
	"cooperation-proposal": {"customer", "needs", "business", "goal"},
	"faq-guide":            {"audience", "topic"},
}

func ValidateProposal(p Proposal) error {
	fields, ok := RequiredFields[p.Scene]
	if !ok || len(p.OutlineMarkdown) < 10 || len(p.OutlineMarkdown) > 30000 || strings.TrimSpace(p.Document.Title) == "" || len(p.Document.Title) > 200 || len(p.Document.Sections) == 0 || len(p.Document.Sections) > 60 || len(p.Sources) == 0 || len(p.Sources) > 30 {
		return ErrInvalid
	}
	if p.Format != "docx" && p.Format != "pptx" && p.Format != "xlsx" && p.Format != "md" {
		return ErrInvalid
	}
	for _, field := range fields {
		if strings.TrimSpace(p.Requirements[field]) == "" {
			return ErrInvalid
		}
	}
	for _, section := range p.Document.Sections {
		if strings.TrimSpace(section.Heading) == "" || len(section.Body) > 30000 || len(section.Notes) > 10000 {
			return ErrInvalid
		}
		for _, table := range section.Tables {
			if len(table.Headers) == 0 || len(table.Headers) > 20 || len(table.Rows) > 300 {
				return ErrInvalid
			}
			for _, row := range table.Rows {
				if len(row) != len(table.Headers) {
					return ErrInvalid
				}
				if p.Format == "xlsx" {
					for _, cell := range row {
						if spreadsheetFormulaText(cell) {
							return ErrTableFormula
						}
					}
				}
			}
			if p.Format == "xlsx" {
				for _, header := range table.Headers {
					if spreadsheetFormulaText(header) {
						return ErrTableFormula
					}
				}
			}
		}
	}
	if p.Budget != nil {
		if len(p.Budget.Items) == 0 || len(p.Budget.Items) > 300 {
			return ErrInvalid
		}
		for _, item := range p.Budget.Items {
			if item.Label == "" || item.Quantity < 0 || item.Quantity > 10000000 || item.UnitPrice < 0 || item.UnitPrice > 100000000 {
				return ErrInvalid
			}
		}
	}
	data, err := json.Marshal(p)
	if err != nil || len(data) > 256*1024 {
		return ErrInvalid
	}
	return nil
}

func spreadsheetFormulaText(value string) bool {
	value = strings.TrimSpace(value)
	value = strings.TrimSpace(strings.TrimLeft(value, "'"))
	return strings.HasPrefix(value, "=")
}

func ParseReview(data string) (Review, error) {
	var result struct {
		Passed *bool    `json:"passed"`
		Issues *[]Issue `json:"issues"`
	}
	d := json.NewDecoder(strings.NewReader(data))
	d.DisallowUnknownFields()
	if d.Decode(&result) != nil || result.Passed == nil || result.Issues == nil {
		return Review{}, ErrInvalid
	}
	var extra interface{}
	if err := d.Decode(&extra); err != io.EOF {
		return Review{}, ErrInvalid
	}
	review := Review{Passed: *result.Passed, Issues: *result.Issues}
	for _, issue := range review.Issues {
		if strings.TrimSpace(issue.Location) == "" || strings.TrimSpace(issue.Problem) == "" || strings.TrimSpace(issue.Suggestion) == "" {
			return Review{}, ErrInvalid
		}
		if issue.Severity != "blocking" && issue.Severity != "suggestion" {
			return Review{}, ErrInvalid
		}
		if issue.Severity == "blocking" {
			review.Passed = false
		}
	}
	return review, nil
}
