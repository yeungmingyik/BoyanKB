package chatpipeline

import (
	"context"
	"testing"

	"github.com/Tencent/WeKnora/internal/models/chat"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"github.com/stretchr/testify/require"
)

type graphEvidenceChatBase = chat.Chat
type graphEvidenceChat struct{ graphEvidenceChatBase }

func (m *graphEvidenceChat) Chat(context.Context, []chat.Message, *chat.ChatOptions) (*types.ChatResponse, error) {
	return &types.ChatResponse{Content: `[{"entity":"课程"}]`}, nil
}

type graphEvidenceModels struct{ interfaces.ModelService }

func (m *graphEvidenceModels) GetChatModel(context.Context, string) (chat.Chat, error) {
	return &graphEvidenceChat{}, nil
}

type graphEvidenceKBRepository struct {
	interfaces.KnowledgeBaseRepository
	kb       *types.KnowledgeBase
	ids      []string
	tenantID uint64
}

func (r *graphEvidenceKBRepository) GetKnowledgeBaseByIDs(ctx context.Context, ids []string) ([]*types.KnowledgeBase, error) {
	r.ids = append([]string(nil), ids...)
	r.tenantID = types.MustTenantIDFromContext(ctx)
	return []*types.KnowledgeBase{r.kb}, nil
}

func TestGraphExtractionSharedKBReachesGraphAndDuplicatePreservesPrimaryMatchType(t *testing.T) {
	t.Setenv("NEO4J_ENABLE", "true")
	plugin, ctx, cm := graphScopeFixture()
	cm.KnowledgeBaseIDs = []string{"hq"}
	cm.Entity = nil
	cm.EntityKBIDs = nil
	cm.Query = "课程与合作学校是什么关系？"
	cm.ChatModelID = "chat"
	repository := &graphEvidenceKBRepository{kb: plugin.knowledgeBaseService.(*graphScopeKBService).kb}
	extraction := &PluginExtractEntity{modelService: &graphEvidenceModels{}, knowledgeBaseRepo: repository, template: &types.PromptTemplateStructured{Description: "抽取实体"}}
	manager := NewEventManager()
	manager.Register(&PluginQueryUnderstand{})
	manager.Register(extraction)
	require.Nil(t, manager.Trigger(ctx, types.QUERY_UNDERSTAND, cm))
	require.Equal(t, uint64(10005), repository.tenantID)
	require.Equal(t, []string{"hq"}, repository.ids)
	require.Equal(t, []string{"hq"}, cm.EntityKBIDs)
	require.Equal(t, []string{"课程"}, cm.Entity)
	graphCM := cm.Clone()
	runGraphScope(t, plugin, ctx, graphCM)
	require.Len(t, graphCM.SearchResult, 1)
	require.Equal(t, types.MatchTypeGraph, graphCM.SearchResult[0].MatchType)
	vector := *graphCM.SearchResult[0]
	vector.MatchType = types.MatchTypeEmbedding
	merged := removeDuplicateResults(append([]*types.SearchResult{&vector}, graphCM.SearchResult...))
	require.Len(t, merged, 1)
	require.Equal(t, types.MatchTypeEmbedding, merged[0].MatchType)
}
