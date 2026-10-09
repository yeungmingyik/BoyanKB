package service

import (
	"context"
	"errors"
	"testing"

	"github.com/Tencent/WeKnora/internal/models/rerank"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

func TestBoyanHybridSearchRequiresReadyBoundAgent(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:"+uuid.NewString()+"?mode=memory&cache=shared"), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	require.NoError(t, db.AutoMigrate(&types.PartnerProvisioningJob{}))
	sqlDB, err := db.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = sqlDB.Close() })
	job := types.PartnerProvisioningJob{PartnerKey: "test", TenantID: 42, OwnerUserID: "owner", Status: "ready", HQKnowledgeBaseIDs: types.StringArray{}, State: types.PartnerProvisioningState{AgentIDs: map[string]string{"mentor": "a", "planner": "b", "reviewer": "c", "other": "d"}}}
	require.NoError(t, db.Create(&job).Error)
	svc := &agentService{db: db}
	ctx := context.WithValue(context.Background(), types.TenantIDContextKey, uint64(42))
	base := types.CustomAgent{ID: "b", TenantID: 42, CreatedBy: "owner", Config: types.CustomAgentConfig{BoyanHybridSearchWithoutRerank: true}}
	for _, id := range []string{"a", "b", "c"} {
		agent := base
		agent.ID = id
		require.True(t, svc.allowsBoyanHybridSearchWithoutRerank(ctx, &agent))
	}
	for _, tc := range []struct {
		name string
		edit func(*types.CustomAgent)
	}{
		{"flag required", func(a *types.CustomAgent) { a.Config.BoyanHybridSearchWithoutRerank = false }},
		{"unbound agent", func(a *types.CustomAgent) { a.ID = "unbound" }},
		{"other role", func(a *types.CustomAgent) { a.ID = "d" }},
		{"other tenant", func(a *types.CustomAgent) { a.TenantID = 43 }},
		{"other creator", func(a *types.CustomAgent) { a.CreatedBy = "other" }},
		{"missing creator", func(a *types.CustomAgent) { a.CreatedBy = "" }},
		{"configured model", func(a *types.CustomAgent) { a.Config.RerankModelID = "configured" }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			agent := base
			tc.edit(&agent)
			require.False(t, svc.allowsBoyanHybridSearchWithoutRerank(ctx, &agent))
		})
	}
	require.False(t, svc.allowsBoyanHybridSearchWithoutRerank(context.Background(), &base))
	require.False(t, svc.allowsBoyanHybridSearchWithoutRerank(context.WithValue(ctx, types.TenantIDContextKey, uint64(43)), &base))
	require.False(t, svc.allowsBoyanHybridSearchWithoutRerank(ctx, nil))
	require.False(t, (&agentService{}).allowsBoyanHybridSearchWithoutRerank(ctx, &base))
	for _, status := range []string{"provisioning", "failed", "pending"} {
		require.NoError(t, db.Model(&job).Update("status", status).Error)
		require.False(t, svc.allowsBoyanHybridSearchWithoutRerank(ctx, &base))
	}
	require.NoError(t, db.Model(&job).Update("status", "ready").Error)
	require.NoError(t, sqlDB.Close())
	require.False(t, svc.allowsBoyanHybridSearchWithoutRerank(ctx, &base))
}

type boyanRerankPolicyStub struct {
	interfaces.AgentService
	allowed bool
}

func (s boyanRerankPolicyStub) allowsBoyanHybridSearchWithoutRerank(context.Context, *types.CustomAgent) bool {
	return s.allowed
}

type boyanRerankModelStub struct {
	interfaces.ModelService
	model rerank.Reranker
	err   error
	calls int
}

func (s *boyanRerankModelStub) GetRerankModel(context.Context, string) (rerank.Reranker, error) {
	s.calls++
	return s.model, s.err
}

type boyanRerankerStub struct{ rerank.Reranker }

func TestBoyanOptionalRerankPreservesModelFailures(t *testing.T) {
	for _, tc := range []struct {
		name      string
		flag      bool
		verified  bool
		modelID   string
		model     rerank.Reranker
		modelErr  error
		wantErr   bool
		wantCalls int
	}{
		{name: "ordinary agent requires rerank", wantErr: true},
		{name: "flag alone cannot bypass", flag: true, wantErr: true},
		{name: "verification requires opt in", verified: true, wantErr: true},
		{name: "managed agent can use hybrid", flag: true, verified: true},
		{name: "configured failure remains failure", flag: true, verified: true, modelID: "model", modelErr: errors.New("unavailable"), wantErr: true, wantCalls: 1},
		{name: "configured nil remains failure", flag: true, verified: true, modelID: "model", wantErr: true, wantCalls: 1},
		{name: "configured model still used", flag: true, verified: true, modelID: "model", model: &boyanRerankerStub{}, wantCalls: 1},
		{name: "ordinary configured model", modelID: "model", model: &boyanRerankerStub{}, wantCalls: 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			models := &boyanRerankModelStub{model: tc.model, err: tc.modelErr}
			svc := &sessionService{agentService: boyanRerankPolicyStub{allowed: tc.verified}, modelService: models}
			agent := &types.CustomAgent{Config: types.CustomAgentConfig{KBSelectionMode: "selected", AllowedTools: []string{"search_knowledge"}, BoyanHybridSearchWithoutRerank: tc.flag, RerankModelID: tc.modelID}}
			model, err := svc.resolveAgentReranker(context.Background(), agent)
			require.Equal(t, tc.wantErr, err != nil)
			require.Equal(t, tc.wantCalls, models.calls)
			if !tc.wantErr {
				require.Equal(t, tc.model, model)
			}
		})
	}
	svc := &sessionService{}
	_, err := svc.resolveAgentReranker(context.Background(), &types.CustomAgent{Config: types.CustomAgentConfig{KBSelectionMode: "none"}})
	require.NoError(t, err)
	_, err = svc.resolveAgentReranker(context.Background(), &types.CustomAgent{Config: types.CustomAgentConfig{KBSelectionMode: "selected", AllowedTools: []string{"read_document"}}})
	require.NoError(t, err)
	_, err = svc.resolveAgentReranker(context.Background(), &types.CustomAgent{Config: types.CustomAgentConfig{KBSelectionMode: "selected", AllowedTools: []string{"search_knowledge"}, BoyanHybridSearchWithoutRerank: true}})
	require.Error(t, err)
}
