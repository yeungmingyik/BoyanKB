package types

import (
	"database/sql/driver"
	"encoding/json"
	"fmt"
	"time"
)

type PartnerRestrictedSkill struct {
	Version       string `json:"version"`
	SHA256        string `json:"sha256"`
	CatalogID     string `json:"catalog_id"`
	CatalogSHA256 string `json:"catalog_sha256"`
	Enabled       bool   `json:"enabled"`
}

const PartnerKnowledgeSharedEnterprise = "shared_enterprise"

type PartnerProvisioningState struct {
	KnowledgeMode     string                            `json:"knowledge_mode,omitempty"`
	TeamManagerUserID string                            `json:"team_manager_user_id,omitempty"`
	OrganizationID    string                            `json:"organization_id,omitempty"`
	KnowledgeBaseID   string                            `json:"knowledge_base_id,omitempty"`
	ContributorUserID string                            `json:"contributor_user_id,omitempty"`
	AgentIDs          map[string]string                 `json:"agent_ids,omitempty"`
	SkillCatalogIDs   map[string]string                 `json:"skill_catalog_ids,omitempty"`
	RestrictedSkills  map[string]PartnerRestrictedSkill `json:"restricted_skills,omitempty"`
}

func (s PartnerProvisioningState) Value() (driver.Value, error) { return json.Marshal(s) }

func (s *PartnerProvisioningState) Scan(value any) error {
	if value == nil {
		*s = PartnerProvisioningState{}
		return nil
	}
	var b []byte
	switch v := value.(type) {
	case []byte:
		b = v
	case string:
		b = []byte(v)
	default:
		return fmt.Errorf("invalid partner state type")
	}
	return json.Unmarshal(b, s)
}

type PartnerProvisioningJob struct {
	PartnerKey         string                   `json:"partner_key" gorm:"type:varchar(64);primaryKey"`
	RequestHash        string                   `json:"request_hash" gorm:"type:varchar(64);not null"`
	SpecHash           string                   `json:"spec_hash" gorm:"type:varchar(64);not null"`
	HQTenantID         uint64                   `json:"hq_tenant_id" gorm:"not null"`
	HQKnowledgeBaseIDs StringArray              `json:"hq_knowledge_base_ids" gorm:"type:jsonb;not null"`
	TenantID           uint64                   `json:"tenant_id" gorm:"not null;uniqueIndex"`
	OwnerUserID        string                   `json:"owner_user_id" gorm:"type:varchar(36);not null"`
	Status             string                   `json:"status" gorm:"type:varchar(32);not null"`
	State              PartnerProvisioningState `json:"state" gorm:"type:jsonb;not null"`
	Revision           uint64                   `json:"revision" gorm:"not null"`
	LeaseHolder        string                   `json:"-" gorm:"type:varchar(36);not null;default:''"`
	LeaseUntil         *time.Time               `json:"lease_until,omitempty"`
	CreatedAt          time.Time                `json:"created_at"`
	UpdatedAt          time.Time                `json:"updated_at"`
}

func (PartnerProvisioningJob) TableName() string { return "partner_provisioning_jobs" }

func (j PartnerProvisioningJob) ValidKnowledgeMode() bool {
	switch j.State.KnowledgeMode {
	case "":
		return true
	case PartnerKnowledgeSharedEnterprise:
		return len(j.HQKnowledgeBaseIDs) == 1 && j.HQKnowledgeBaseIDs[0] != "" && j.State.KnowledgeBaseID == "" && j.State.ContributorUserID == ""
	default:
		return false
	}
}
