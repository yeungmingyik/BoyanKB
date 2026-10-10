package teaminvitation

import (
	"errors"

	"github.com/Tencent/WeKnora/internal/types"
	"gorm.io/gorm"
)

func EligibleMember(db *gorm.DB, tenantID uint64, userID string) (bool, error) {
	if tenantID == 0 || userID == "" {
		return false, nil
	}
	var count int64
	err := db.Model(&types.TenantMember{}).
		Joins("JOIN users ON users.id = tenant_members.user_id AND users.deleted_at IS NULL AND users.is_active = ?", true).
		Joins("JOIN tenants ON tenants.id = tenant_members.tenant_id AND tenants.deleted_at IS NULL AND tenants.status = ?", "active").
		Where("tenant_members.tenant_id = ? AND tenant_members.user_id = ? AND tenant_members.role = ? AND tenant_members.status = ?", tenantID, userID, types.TenantRoleViewer, types.TenantMemberStatusActive).
		Count(&count).Error
	return count == 1, err
}

func Authorized(db *gorm.DB, tenantID uint64, userID string) (bool, error) {
	eligible, err := EligibleMember(db, tenantID, userID)
	if err != nil || !eligible {
		return false, err
	}
	var job types.PartnerProvisioningJob
	err = db.Where("tenant_id = ? AND status = ?", tenantID, "ready").Take(&job).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	return job.ValidKnowledgeMode() && job.State.KnowledgeMode == types.PartnerKnowledgeSharedEnterprise && job.State.TeamManagerUserID == userID, nil
}

func InvitationAllowed(db *gorm.DB, invitation *types.TenantInvitation) (bool, error) {
	if invitation.TeamManagerUserID == "" {
		return true, nil
	}
	if invitation.Role != types.TenantRoleViewer || invitation.InvitedBy == nil || *invitation.InvitedBy != invitation.TeamManagerUserID {
		return false, nil
	}
	return Authorized(db, invitation.TenantID, invitation.TeamManagerUserID)
}
