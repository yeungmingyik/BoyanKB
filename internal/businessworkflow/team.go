package businessworkflow

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"errors"
	"net/mail"
	"strconv"
	"strings"
	"time"

	"github.com/Tencent/WeKnora/internal/teaminvitation"
	"github.com/Tencent/WeKnora/internal/types"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
	"gorm.io/gorm/logger"
)

type TeamMember struct {
	UserID   string           `json:"user_id"`
	Username string           `json:"username"`
	Role     types.TenantRole `json:"role"`
}

type TeamCapability struct {
	Available bool         `json:"available"`
	Reason    string       `json:"reason"`
	Members   []TeamMember `json:"members,omitempty"`
}

type TeamInvitation struct {
	ID            uint64                       `json:"id"`
	Kind          string                       `json:"kind"`
	InviteeName   string                       `json:"invitee_name,omitempty"`
	Status        types.TenantInvitationStatus `json:"status"`
	ExpiresAt     time.Time                    `json:"expires_at"`
	CreatedAt     time.Time                    `json:"created_at"`
	AcceptedCount int                          `json:"accepted_count"`
	Token         string                       `json:"token,omitempty"`
	InviteURL     string                       `json:"invite_url,omitempty"`
}

type TeamInvitations struct {
	Items []TeamInvitation `json:"items"`
	Total int64            `json:"total"`
}

type TeamInvitationInput struct {
	Kind  string `json:"kind"`
	Email string `json:"email,omitempty"`
}

func (s *Service) teamManager(ctx context.Context, db *gorm.DB) (types.Caller, error) {
	if _, apiKey := types.TenantAPIKeyScopeFromContext(ctx); apiKey {
		return types.Caller{}, ErrDenied
	}
	c, err := s.skillCatalogCaller(ctx)
	if err != nil {
		return c, err
	}
	allowed, err := teaminvitation.Authorized(db.WithContext(ctx), c.TenantID, c.UserID)
	if err != nil {
		return c, ErrUnavailable
	}
	if !allowed {
		return c, ErrDenied
	}
	return c, nil
}

func (s *Service) Team(ctx context.Context) (TeamCapability, error) {
	if _, apiKey := types.TenantAPIKeyScopeFromContext(ctx); apiKey {
		return TeamCapability{}, ErrDenied
	}
	c, err := s.skillCatalogCaller(ctx)
	if err != nil {
		return TeamCapability{}, err
	}
	result := TeamCapability{Reason: "当前账号没有本工作空间的团队邀请权限。"}
	allowed, err := teaminvitation.Authorized(s.DB.WithContext(ctx), c.TenantID, c.UserID)
	if err != nil {
		return TeamCapability{}, ErrUnavailable
	}
	if !allowed {
		return result, nil
	}
	result.Available, result.Reason = true, "可邀请只读员工加入当前工作空间；员工可使用问答、业务技能及文档。"
	err = s.DB.WithContext(ctx).Model(&types.TenantMember{}).
		Select("tenant_members.user_id, users.username, tenant_members.role").
		Joins("JOIN users ON users.id = tenant_members.user_id AND users.deleted_at IS NULL AND users.is_active = ?", true).
		Where("tenant_members.tenant_id = ? AND tenant_members.role = ? AND tenant_members.status = ?", c.TenantID, types.TenantRoleViewer, types.TenantMemberStatusActive).
		Order("tenant_members.id").Limit(1000).Scan(&result.Members).Error
	if err != nil {
		return TeamCapability{}, ErrUnavailable
	}
	return result, nil
}

func publicTeamInvitation(inv types.TenantInvitation) TeamInvitation {
	item := TeamInvitation{ID: inv.ID, Kind: "email", Status: inv.Status, ExpiresAt: inv.ExpiresAt, CreatedAt: inv.CreatedAt, AcceptedCount: inv.AcceptedCount}
	if inv.InviteeUserID == "" {
		item.Kind = "link"
	}
	if item.Status == types.TenantInvitationStatusPending && inv.IsExpired(time.Now()) {
		item.Status = types.TenantInvitationStatusExpired
	}
	return item
}

func (s *Service) TeamInvitations(ctx context.Context) (TeamInvitations, error) {
	c, err := s.teamManager(ctx, s.DB)
	if err != nil {
		return TeamInvitations{}, err
	}
	query := s.DB.WithContext(ctx).Model(&types.TenantInvitation{}).Where("tenant_id = ? AND team_manager_user_id = ? AND invited_by = ? AND role = ?", c.TenantID, c.UserID, c.UserID, types.TenantRoleViewer)
	result := TeamInvitations{Items: []TeamInvitation{}}
	if err := query.Count(&result.Total).Error; err != nil {
		return result, ErrUnavailable
	}
	var invitations []types.TenantInvitation
	if err := query.Select("id", "invitee_user_id", "status", "expires_at", "created_at", "accepted_count").Order("id DESC").Limit(100).Find(&invitations).Error; err != nil {
		return result, ErrUnavailable
	}
	for _, invitation := range invitations {
		item := publicTeamInvitation(invitation)
		if invitation.InviteeUserID != "" {
			var user types.User
			if err := s.DB.WithContext(ctx).Select("username").Where("id = ?", invitation.InviteeUserID).Take(&user).Error; err == nil {
				item.InviteeName = user.Username
			} else if !errors.Is(err, gorm.ErrRecordNotFound) {
				return result, ErrUnavailable
			}
		}
		result.Items = append(result.Items, item)
	}
	return result, nil
}

func lockTeamManager(tx *gorm.DB, c types.Caller) error {
	var job types.PartnerProvisioningJob
	if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).Where("tenant_id = ?", c.TenantID).Take(&job).Error; err != nil {
		return ErrDenied
	}
	var member types.TenantMember
	if err := tx.Clauses(clause.Locking{Strength: "SHARE"}).Where("tenant_id = ? AND user_id = ?", c.TenantID, c.UserID).Take(&member).Error; err != nil {
		return ErrDenied
	}
	allowed, err := teaminvitation.Authorized(tx, c.TenantID, c.UserID)
	if err != nil {
		return ErrUnavailable
	}
	if !allowed {
		return ErrDenied
	}
	return nil
}

func teamAudit(tx *gorm.DB, c types.Caller, invitation types.TenantInvitation, action types.AuditAction) error {
	return tx.Create(&types.AuditLog{TenantID: c.TenantID, ActorUserID: c.UserID, ActorRole: string(types.TenantRoleViewer), Action: action, TargetType: "tenant_invitation", TargetID: strconv.FormatUint(invitation.ID, 10), TargetUserID: invitation.InviteeUserID, Outcome: types.AuditOutcomeSuccess, Details: types.JSON(`{"role":"viewer","source":"business_team"}`)}).Error
}

func (s *Service) CreateTeamInvitation(ctx context.Context, input TeamInvitationInput) (TeamInvitation, error) {
	c, err := s.teamManager(ctx, s.DB)
	if err != nil {
		return TeamInvitation{}, err
	}
	input.Email = strings.ToLower(strings.TrimSpace(input.Email))
	if input.Kind != "link" && input.Kind != "email" || input.Kind == "link" && input.Email != "" {
		return TeamInvitation{}, ErrInvalid
	}
	if input.Kind == "email" {
		address, err := mail.ParseAddress(input.Email)
		if err != nil || address.Address != input.Email || len(input.Email) > 254 {
			return TeamInvitation{}, ErrInvalid
		}
	}
	var result TeamInvitation
	err = s.DB.WithContext(ctx).Session(&gorm.Session{Logger: logger.Discard}).Transaction(func(tx *gorm.DB) error {
		if err := lockTeamManager(tx, c); err != nil {
			return err
		}
		if err := tx.Model(&types.TenantInvitation{}).Where("tenant_id = ? AND team_manager_user_id = ? AND status = ? AND expires_at <= ?", c.TenantID, c.UserID, types.TenantInvitationStatusPending, time.Now()).Updates(map[string]any{"status": types.TenantInvitationStatusExpired, "responded_at": time.Now().UTC()}).Error; err != nil {
			return err
		}
		var pending int64
		if err := tx.Model(&types.TenantInvitation{}).Where("tenant_id = ? AND team_manager_user_id = ? AND status = ? AND expires_at > ?", c.TenantID, c.UserID, types.TenantInvitationStatusPending, time.Now()).Count(&pending).Error; err != nil {
			return err
		}
		if pending >= 20 {
			return ErrConflict
		}
		invitation := types.TenantInvitation{TenantID: c.TenantID, TeamManagerUserID: c.UserID, InvitedBy: &c.UserID, Role: types.TenantRoleViewer, Status: types.TenantInvitationStatusPending, ExpiresAt: time.Now().UTC().Add(24 * time.Hour)}
		name := ""
		if input.Kind == "email" {
			var user types.User
			if err := tx.Where("LOWER(email) = ? AND is_active = ?", input.Email, true).Take(&user).Error; err != nil {
				if errors.Is(err, gorm.ErrRecordNotFound) {
					return ErrInvalid
				}
				return err
			}
			var existing int64
			if err := tx.Model(&types.TenantMember{}).Where("tenant_id = ? AND user_id = ?", c.TenantID, user.ID).Count(&existing).Error; err != nil {
				return err
			}
			if existing != 0 {
				return ErrConflict
			}
			if err := tx.Model(&types.TenantInvitation{}).Where("tenant_id = ? AND invitee_user_id = ? AND status = ?", c.TenantID, user.ID, types.TenantInvitationStatusPending).Count(&existing).Error; err != nil {
				return err
			}
			if existing != 0 {
				return ErrConflict
			}
			invitation.InviteeUserID, name = user.ID, user.Username
		} else {
			var secret [32]byte
			if _, err := rand.Read(secret[:]); err != nil {
				return err
			}
			invitation.Token = base64.RawURLEncoding.EncodeToString(secret[:])
		}
		if err := tx.Create(&invitation).Error; err != nil {
			return err
		}
		if err := teamAudit(tx, c, invitation, types.AuditActionInvitationSent); err != nil {
			return err
		}
		result = publicTeamInvitation(invitation)
		result.InviteeName = name
		if invitation.Token != "" {
			result.Token, result.InviteURL = invitation.Token, "/register?token="+invitation.Token
		}
		return nil
	})
	if err != nil {
		if errors.Is(err, ErrDenied) || errors.Is(err, ErrInvalid) || errors.Is(err, ErrConflict) {
			return TeamInvitation{}, err
		}
		return TeamInvitation{}, ErrUnavailable
	}
	return result, nil
}

func (s *Service) RevokeTeamInvitation(ctx context.Context, id uint64) error {
	c, err := s.teamManager(ctx, s.DB)
	if err != nil {
		return err
	}
	return s.DB.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		if err := lockTeamManager(tx, c); err != nil {
			return err
		}
		var invitation types.TenantInvitation
		err := tx.Select("id", "invitee_user_id").Where("id = ? AND tenant_id = ? AND team_manager_user_id = ? AND invited_by = ? AND role = ? AND status = ?", id, c.TenantID, c.UserID, c.UserID, types.TenantRoleViewer, types.TenantInvitationStatusPending).Take(&invitation).Error
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return ErrDenied
		}
		if err != nil {
			return ErrUnavailable
		}
		result := tx.Model(&types.TenantInvitation{}).Where("id = ? AND status = ?", id, types.TenantInvitationStatusPending).Updates(map[string]any{"status": types.TenantInvitationStatusRevoked, "responded_at": time.Now().UTC(), "token": ""})
		if result.Error != nil || result.RowsAffected != 1 {
			return ErrConflict
		}
		return teamAudit(tx, c, invitation, types.AuditActionInvitationRevoked)
	})
}
