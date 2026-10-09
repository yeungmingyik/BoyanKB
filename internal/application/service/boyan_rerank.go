package service

import (
	"context"
	"errors"
	"fmt"

	"github.com/Tencent/WeKnora/internal/models/rerank"
	"github.com/Tencent/WeKnora/internal/types"
)

type boyanHybridSearchPolicy interface {
	allowsBoyanHybridSearchWithoutRerank(context.Context, *types.CustomAgent) bool
}

func (s *agentService) allowsBoyanHybridSearchWithoutRerank(ctx context.Context, agent *types.CustomAgent) bool {
	if s.db == nil || agent == nil || !agent.Config.BoyanHybridSearchWithoutRerank || agent.Config.RerankModelID != "" || agent.ID == "" || agent.CreatedBy == "" {
		return false
	}
	tenantID, ok := ctx.Value(types.TenantIDContextKey).(uint64)
	if !ok || tenantID == 0 || tenantID != agent.TenantID {
		return false
	}
	var job types.PartnerProvisioningJob
	if err := s.db.WithContext(ctx).Where("tenant_id = ? AND status = ?", tenantID, "ready").First(&job).Error; err != nil {
		return false
	}
	if job.OwnerUserID != agent.CreatedBy {
		return false
	}
	for _, role := range []string{"mentor", "planner", "reviewer"} {
		if job.State.AgentIDs[role] == agent.ID {
			return true
		}
	}
	return false
}

func (s *sessionService) resolveAgentReranker(ctx context.Context, agent *types.CustomAgent) (rerank.Reranker, error) {
	if !agentRequiresRerankModel(agent) {
		return nil, nil
	}
	if agent.Config.RerankModelID == "" {
		policy, ok := s.agentService.(boyanHybridSearchPolicy)
		if ok && agent.Config.BoyanHybridSearchWithoutRerank && policy.allowsBoyanHybridSearchWithoutRerank(ctx, agent) {
			return nil, nil
		}
		return nil, errors.New("rerank model is not configured: please set rerank_model_id on the agent")
	}
	model, err := s.modelService.GetRerankModel(ctx, agent.Config.RerankModelID)
	if err != nil {
		return nil, fmt.Errorf("failed to get rerank model: %w", err)
	}
	if model == nil {
		return nil, errors.New("configured rerank model is unavailable")
	}
	return model, nil
}
