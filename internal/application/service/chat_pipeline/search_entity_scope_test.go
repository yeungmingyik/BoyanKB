package chatpipeline

import (
	"context"
	"errors"
	"testing"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"github.com/stretchr/testify/require"
)

type graphScopeKBService struct {
	interfaces.KnowledgeBaseService
	kb *types.KnowledgeBase
}

func (s *graphScopeKBService) GetKnowledgeBaseByIDOnly(context.Context, string) (*types.KnowledgeBase, error) {
	return s.kb, nil
}

type graphScopeShares struct {
	interfaces.KBShareService
	allowed bool
	err     error
	calls   int
}

func (s *graphScopeShares) CheckTenantKBPermission(ctx context.Context, id string, tenantID uint64, role types.TenantRole) (types.OrgMemberRole, bool, error) {
	s.calls++
	if types.CallerFromContext(ctx).UserID != "partner" || id != "hq" || tenantID != 10005 || role != types.TenantRoleViewer {
		return "", false, errors.New("invalid caller")
	}
	return types.OrgRoleViewer, s.allowed, s.err
}

type graphScopeRepository struct {
	interfaces.RetrieveGraphRepository
	graph     *types.GraphData
	calls     []types.NameSpace
	afterRead func()
}

func (s *graphScopeRepository) SearchNode(_ context.Context, namespace types.NameSpace, _ []string) (*types.GraphData, error) {
	s.calls = append(s.calls, namespace)
	if s.afterRead != nil {
		s.afterRead()
	}
	return s.graph, nil
}

type graphScopeChunkRepository struct {
	interfaces.ChunkRepository
	rows      []*types.Chunk
	tenantIDs []uint64
	callers   []types.Caller
}

func (s *graphScopeChunkRepository) ListChunksByID(ctx context.Context, tenantID uint64, _ []string) ([]*types.Chunk, error) {
	s.tenantIDs = append(s.tenantIDs, tenantID)
	s.callers = append(s.callers, types.CallerFromContext(ctx))
	if tenantID != 10000 || types.MustTenantIDFromContext(ctx) != 10000 {
		return nil, errors.New("incorrect source")
	}
	return s.rows, nil
}

func (s *graphScopeChunkRepository) ListChunksByParentIDs(context.Context, uint64, []string) ([]*types.Chunk, error) {
	return nil, nil
}

type graphScopeKnowledgeRepository struct {
	interfaces.KnowledgeRepository
	rows      []*types.Knowledge
	tenantIDs []uint64
}

func (s *graphScopeKnowledgeRepository) GetKnowledgeBatch(_ context.Context, tenantID uint64, _ []string) ([]*types.Knowledge, error) {
	s.tenantIDs = append(s.tenantIDs, tenantID)
	return s.rows, nil
}

func graphScopeFixture() (*PluginSearchEntity, context.Context, *types.ChatManage) {
	kb := &types.KnowledgeBase{ID: "hq", TenantID: 10000, IndexingStrategy: types.IndexingStrategy{GraphEnabled: true}, ExtractConfig: &types.ExtractConfig{Enabled: true}}
	plugin := &PluginSearchEntity{
		knowledgeBaseService: &graphScopeKBService{kb: kb},
		shares:               &graphScopeShares{allowed: true},
		graphRepo:            &graphScopeRepository{graph: &types.GraphData{Node: []*types.GraphNode{{Name: "课程", Chunks: []string{"c1"}}}}},
		chunkRepo:            &graphScopeChunkRepository{rows: []*types.Chunk{{ID: "c1", TenantID: 10000, KnowledgeBaseID: "hq", KnowledgeID: "d1", IsEnabled: true, Content: "已授权的课程资料"}}},
		knowledgeRepo:        &graphScopeKnowledgeRepository{rows: []*types.Knowledge{{ID: "d1", TenantID: 10000, KnowledgeBaseID: "hq", Title: "课程方案"}}},
	}
	ctx := types.WithCaller(context.Background(), types.Caller{TenantID: 10005, UserID: "partner", Role: types.TenantRoleViewer})
	ctx = types.WithExecutionTenant(ctx, 10005)
	cm := &types.ChatManage{
		PipelineRequest: types.PipelineRequest{SearchTargets: types.SearchTargets{{Type: types.SearchTargetTypeKnowledgeBase, KnowledgeBaseID: "hq", TenantID: 10000}}},
		PipelineState:   types.PipelineState{Entity: []string{"课程"}, EntityKBIDs: []string{"hq"}},
	}
	return plugin, ctx, cm
}

func runGraphScope(t *testing.T, plugin *PluginSearchEntity, ctx context.Context, cm *types.ChatManage) {
	t.Helper()
	next := false
	require.Nil(t, plugin.OnEvent(ctx, types.ENTITY_SEARCH, cm, func() *PluginError { next = true; return nil }))
	require.True(t, next)
}

func TestGraphEntitySharedKBUsesAuthorizedSourceTenant(t *testing.T) {
	plugin, ctx, cm := graphScopeFixture()
	runGraphScope(t, plugin, ctx, cm)
	require.Len(t, cm.SearchResult, 1)
	require.Equal(t, "c1", cm.SearchResult[0].ID)
	require.Equal(t, types.MatchTypeGraph, cm.SearchResult[0].MatchType)
	chunks := plugin.chunkRepo.(*graphScopeChunkRepository)
	require.Equal(t, []uint64{10000}, chunks.tenantIDs)
	require.Equal(t, uint64(10005), chunks.callers[0].TenantID)
	require.Equal(t, "partner", chunks.callers[0].UserID)
	require.Equal(t, []uint64{10000}, plugin.knowledgeRepo.(*graphScopeKnowledgeRepository).tenantIDs)
	require.Equal(t, uint64(10005), types.MustTenantIDFromContext(ctx))
}

func TestGraphEntityUnsharedAndForgedScopeNeverReadGraph(t *testing.T) {
	for _, scenario := range []string{"unshared", "lookup_error", "no_user", "forged_tenant", "other_kb", "empty_scope"} {
		t.Run(scenario, func(t *testing.T) {
			plugin, ctx, cm := graphScopeFixture()
			switch scenario {
			case "unshared":
				plugin.shares.(*graphScopeShares).allowed = false
			case "lookup_error":
				plugin.shares.(*graphScopeShares).err = errors.New("offline")
			case "no_user":
				ctx = types.WithCaller(ctx, types.Caller{TenantID: 10005, Role: types.TenantRoleViewer})
			case "forged_tenant":
				cm.SearchTargets[0].TenantID = 10005
			case "other_kb":
				cm.SearchTargets[0].KnowledgeBaseID = "other"
			case "empty_scope":
				cm.SearchTargets = nil
			}
			runGraphScope(t, plugin, ctx, cm)
			require.Empty(t, cm.SearchResult)
			require.Empty(t, plugin.graphRepo.(*graphScopeRepository).calls)
			require.Empty(t, plugin.chunkRepo.(*graphScopeChunkRepository).tenantIDs)
		})
	}
}

func TestGraphEntityRejectsCrossKBDisabledAndUnexpectedChunks(t *testing.T) {
	plugin, ctx, cm := graphScopeFixture()
	chunks := plugin.chunkRepo.(*graphScopeChunkRepository)
	for _, id := range []string{"other-kb", "other-tenant", "disabled", "missing-doc", "unexpected"} {
		chunk := *chunks.rows[0]
		chunk.ID = id
		chunk.Content = id
		switch id {
		case "other-kb":
			chunk.KnowledgeBaseID = "private"
		case "other-tenant":
			chunk.TenantID = 20000
		case "disabled":
			chunk.IsEnabled = false
		case "missing-doc":
			chunk.KnowledgeID = "missing"
		}
		chunks.rows = append(chunks.rows, &chunk)
		if id != "unexpected" {
			plugin.graphRepo.(*graphScopeRepository).graph.Node[0].Chunks = append(plugin.graphRepo.(*graphScopeRepository).graph.Node[0].Chunks, id)
		}
	}
	runGraphScope(t, plugin, ctx, cm)
	require.Len(t, cm.SearchResult, 1)
	require.Equal(t, "c1", cm.SearchResult[0].ID)
}

func TestGraphEntityKeepsSelectedDocumentScope(t *testing.T) {
	plugin, ctx, cm := graphScopeFixture()
	cm.SearchTargets[0].Type = types.SearchTargetTypeKnowledge
	cm.SearchTargets[0].KnowledgeIDs = []string{"d1"}
	cm.SearchTargets[0].ScopeTagIDs = []string{"resolved-tag"}
	plugin.graphRepo.(*graphScopeRepository).graph.Node[0].Chunks = append(plugin.graphRepo.(*graphScopeRepository).graph.Node[0].Chunks, "c2")
	plugin.chunkRepo.(*graphScopeChunkRepository).rows = append(plugin.chunkRepo.(*graphScopeChunkRepository).rows, &types.Chunk{ID: "c2", TenantID: 10000, KnowledgeBaseID: "hq", KnowledgeID: "d2", IsEnabled: true, Content: "不在选中文档中的资料"})
	plugin.knowledgeRepo.(*graphScopeKnowledgeRepository).rows = append(plugin.knowledgeRepo.(*graphScopeKnowledgeRepository).rows, &types.Knowledge{ID: "d2", TenantID: 10000, KnowledgeBaseID: "hq"})
	runGraphScope(t, plugin, ctx, cm)
	require.Len(t, cm.SearchResult, 1)
	require.Equal(t, "d1", cm.SearchResult[0].KnowledgeID)
}

func TestGraphEntityRevokedShareDoesNotReturnChunks(t *testing.T) {
	plugin, ctx, cm := graphScopeFixture()
	plugin.graphRepo.(*graphScopeRepository).afterRead = func() { plugin.shares.(*graphScopeShares).allowed = false }
	runGraphScope(t, plugin, ctx, cm)
	require.Empty(t, cm.SearchResult)
	require.Empty(t, plugin.chunkRepo.(*graphScopeChunkRepository).tenantIDs)
}

func TestGraphEntityMissingOrMismatchedKnowledgeDoesNotPanicOrLeak(t *testing.T) {
	for _, row := range []*types.Knowledge{nil, {ID: "d1", TenantID: 10000, KnowledgeBaseID: "other"}, {ID: "d1", TenantID: 20000, KnowledgeBaseID: "hq"}} {
		plugin, ctx, cm := graphScopeFixture()
		plugin.knowledgeRepo.(*graphScopeKnowledgeRepository).rows = []*types.Knowledge{row}
		runGraphScope(t, plugin, ctx, cm)
		require.Empty(t, cm.SearchResult)
	}
}
