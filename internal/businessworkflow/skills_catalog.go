package businessworkflow

import (
	"context"
	"errors"

	"github.com/Tencent/WeKnora/internal/boyanskills"
	"github.com/Tencent/WeKnora/internal/types"
	"gorm.io/gorm"
)

type BusinessSkill struct {
	Name         string `json:"name"`
	DisplayName  string `json:"display_name"`
	Description  string `json:"description"`
	Kind         string `json:"kind"`
	Version      string `json:"version"`
	Installed    bool   `json:"installed"`
	Available    bool   `json:"available"`
	HashVerified bool   `json:"hash_verified"`
	SHA256       string `json:"sha256,omitempty"`
	Status       string `json:"status"`
	Reason       string `json:"reason"`
}

type BusinessSkillCatalog struct {
	Installed      bool            `json:"installed"`
	Available      bool            `json:"available"`
	Status         string          `json:"status"`
	Reason         string          `json:"reason"`
	PlannerAgentID string          `json:"planner_agent_id,omitempty"`
	Skills         []BusinessSkill `json:"skills"`
}

type BusinessSkillDetail struct {
	BusinessSkill
	Content string `json:"content"`
}

var businessSkillDefinitions = []BusinessSkill{
	{Name: "boyan-courseware", DisplayName: "课程讲义", Kind: "scene", Description: "根据授权资料编写课程讲义，明确对象、目标、内容和教学安排。"},
	{Name: "boyan-event-plan", DisplayName: "活动方案", Kind: "scene", Description: "整理活动目标、流程、分工、物料与预算假设。"},
	{Name: "boyan-local-promotion-plan", DisplayName: "本地推广方案", Kind: "scene", Description: "规划本地推广对象、渠道、执行步骤与效果复盘。"},
	{Name: "boyan-cooperation-proposal", DisplayName: "合作提案", Kind: "scene", Description: "编写合作目标、职责边界、交付内容与待确认事项。"},
	{Name: "boyan-faq-guide", DisplayName: "常见问题指南", Kind: "scene", Description: "依据可核实资料整理问答，标注未知信息与待核实事项。"},
	{Name: "boyan-docx", DisplayName: "文字文档", Kind: "format", Description: "将已确认内容组织成结构清晰的文字文档。"},
	{Name: "boyan-pptx", DisplayName: "演示文稿", Kind: "format", Description: "将已确认内容组织成层次清晰的演示文稿。"},
	{Name: "boyan-xlsx", DisplayName: "电子表格", Kind: "format", Description: "编排工作表、预算输入及可核验的计算公式。"},
	{Name: "boyan-md", DisplayName: "轻量文本", Kind: "format", Description: "输出便于阅读、复制和继续编辑的结构化文本。"},
}

func (s *Service) skillCatalogCaller(ctx context.Context) (types.Caller, error) {
	c := types.CallerFromContext(ctx)
	p, ok := types.PrincipalFromContext(ctx)
	if !ok || p.Type != types.PrincipalWebUser || p.ID != c.UserID || c.TenantID == 0 || c.UserID == "" {
		return c, ErrDenied
	}
	if s.DB == nil {
		return c, ErrUnavailable
	}
	var member types.TenantMember
	err := s.DB.WithContext(ctx).Where("tenant_id = ? AND user_id = ? AND status = ?", c.TenantID, c.UserID, types.TenantMemberStatusActive).Take(&member).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return c, ErrDenied
	}
	if err != nil {
		return c, ErrUnavailable
	}
	if !member.Role.IsValid() || !member.Role.HasPermission(types.TenantRoleViewer) {
		return c, ErrDenied
	}
	return c, nil
}

func (s *Service) SkillCatalog(ctx context.Context) (BusinessSkillCatalog, error) {
	c, err := s.skillCatalogCaller(ctx)
	if err != nil {
		return BusinessSkillCatalog{}, err
	}
	result := BusinessSkillCatalog{Status: "not_installed", Reason: "当前工作空间尚未开通业务技能，请联系管理员。", Skills: make([]BusinessSkill, 0, len(businessSkillDefinitions))}
	var job types.PartnerProvisioningJob
	err = s.DB.WithContext(ctx).Where("tenant_id = ?", c.TenantID).Take(&job).Error
	if err != nil && !errors.Is(err, gorm.ErrRecordNotFound) {
		return BusinessSkillCatalog{}, ErrUnavailable
	}
	hasJob := err == nil
	plannerValid := false
	if hasJob {
		result.Status, result.Reason = "not_ready", "业务技能开通尚未完成，请联系管理员。"
		plannerID := job.State.AgentIDs["planner"]
		if plannerID != "" && job.OwnerUserID != "" {
			var planner types.CustomAgent
			err := s.DB.WithContext(ctx).Select("id").Where("id = ? AND tenant_id = ? AND created_by = ? AND is_builtin = ?", plannerID, c.TenantID, job.OwnerUserID, false).Take(&planner).Error
			if err != nil && !errors.Is(err, gorm.ErrRecordNotFound) {
				return BusinessSkillCatalog{}, ErrUnavailable
			}
			if err == nil {
				plannerValid = true
				result.PlannerAgentID = planner.ID
			}
		}
		if job.Status == "ready" {
			result.Status, result.Reason = "unavailable", "业务技能配置或内容校验未通过，请联系管理员。"
		}
	}
	allInstalled, allAvailable := true, true
	for _, definition := range businessSkillDefinitions {
		item := definition
		item.Version = boyanskills.Version
		item.Status, item.Reason = result.Status, result.Reason
		installation, exists := job.State.RestrictedSkills[item.Name]
		item.Installed = hasJob && exists && installation.CatalogID != ""
		guide, fixedErr := boyanskills.Fixed(item.Name)
		if fixedErr == nil {
			item.SHA256 = guide.SHA256
			item.HashVerified = item.Installed && installation.Version == guide.Version && installation.SHA256 == guide.SHA256
		}
		if hasJob && job.Status == "ready" {
			switch {
			case !item.Installed:
				item.Status, item.Reason = "not_installed", "此技能尚未完成安装登记。"
			case !installation.Enabled:
				item.Status, item.Reason = "unavailable", "此技能已停用。"
			case !item.HashVerified:
				item.Status, item.Reason = "unavailable", "技能内容或版本校验未通过，请联系管理员。"
			case !plannerValid:
				item.Status, item.Reason = "unavailable", "当前工作空间的落地方案官关联无效，请联系管理员。"
			default:
				item.Available = true
				item.Status, item.Reason = "ready", "已安装并通过内容校验，可由落地方案官读取使用。"
			}
		}
		allInstalled = allInstalled && item.Installed
		allAvailable = allAvailable && item.Available
		result.Skills = append(result.Skills, item)
	}
	result.Installed, result.Available = allInstalled, allAvailable
	if allAvailable {
		result.Status, result.Reason = "ready", "九项业务技能均已安装并通过内容校验。"
	}
	return result, nil
}

func (s *Service) SkillDetail(ctx context.Context, name string) (BusinessSkillDetail, error) {
	catalog, err := s.SkillCatalog(ctx)
	if err != nil {
		return BusinessSkillDetail{}, err
	}
	for _, item := range catalog.Skills {
		if item.Name != name {
			continue
		}
		if !item.Available {
			return BusinessSkillDetail{}, ErrDenied
		}
		content, err := boyanskills.Read(ctx, s.DB, types.CallerFromContext(ctx).TenantID, name)
		if err != nil {
			return BusinessSkillDetail{}, ErrDenied
		}
		if digest([]byte(content)) != item.SHA256 {
			return BusinessSkillDetail{}, ErrUnavailable
		}
		return BusinessSkillDetail{BusinessSkill: item, Content: content}, nil
	}
	return BusinessSkillDetail{}, ErrDenied
}
