package chatpipeline

import (
	"context"
	"slices"
	"sync"

	"github.com/Tencent/WeKnora/internal/application/access"
	"github.com/Tencent/WeKnora/internal/logger"
	"github.com/Tencent/WeKnora/internal/searchutil"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
)

// PluginSearch implements search functionality for chat pipeline
type PluginSearchEntity struct {
	graphRepo            interfaces.RetrieveGraphRepository
	chunkRepo            interfaces.ChunkRepository
	knowledgeRepo        interfaces.KnowledgeRepository
	knowledgeBaseService interfaces.KnowledgeBaseService
	shares               interfaces.KBShareService
}

// NewPluginSearchEntity creates a new plugin search entity
func NewPluginSearchEntity(
	eventManager *EventManager,
	graphRepository interfaces.RetrieveGraphRepository,
	chunkRepository interfaces.ChunkRepository,
	knowledgeRepository interfaces.KnowledgeRepository,
	knowledgeBaseService interfaces.KnowledgeBaseService,
	shares interfaces.KBShareService,
) *PluginSearchEntity {
	res := &PluginSearchEntity{
		graphRepo:            graphRepository,
		chunkRepo:            chunkRepository,
		knowledgeRepo:        knowledgeRepository,
		knowledgeBaseService: knowledgeBaseService,
		shares:               shares,
	}
	eventManager.Register(res)
	return res
}

// ActivationEvents returns the list of event types this plugin responds to
func (p *PluginSearchEntity) ActivationEvents() []types.EventType {
	return []types.EventType{types.ENTITY_SEARCH}
}

// OnEvent processes triggered events
func (p *PluginSearchEntity) OnEvent(ctx context.Context,
	eventType types.EventType, chatManage *types.ChatManage, next func() *PluginError,
) *PluginError {
	if len(chatManage.Entity) == 0 || len(chatManage.SearchTargets) == 0 || p.knowledgeBaseService == nil {
		return next()
	}
	sources := make(map[string]*types.KnowledgeBase)
	loadSource := func(kbID string) {
		if sources[kbID] != nil || !chatManage.SearchTargets.ContainsKB(kbID) {
			return
		}
		kb, err := p.knowledgeBaseService.GetKnowledgeBaseByIDOnly(ctx, kbID)
		if err != nil || kb == nil || kb.ID != kbID || !kb.IsGraphEnabled() || !p.canReadGraphSource(ctx, kb) {
			return
		}
		if chatManage.SearchTargets.GetTenantIDForKB(kbID) != kb.TenantID {
			return
		}
		sources[kbID] = kb
	}
	for _, kbID := range chatManage.EntityKBIDs {
		loadSource(kbID)
	}
	for _, kbID := range chatManage.EntityKnowledge {
		loadSource(kbID)
	}

	var namespaces []types.NameSpace
	if len(chatManage.EntityKnowledge) > 0 {
		for knowledgeID, kbID := range chatManage.EntityKnowledge {
			if sources[kbID] != nil && graphTargetAllowsKnowledge(chatManage.SearchTargets, sources[kbID], knowledgeID) {
				namespaces = append(namespaces, types.NameSpace{KnowledgeBase: kbID, Knowledge: knowledgeID})
			}
		}
	} else {
		for kbID := range sources {
			namespaces = append(namespaces, types.NameSpace{KnowledgeBase: kbID})
		}
	}
	var wg sync.WaitGroup
	var mu sync.Mutex
	graphs := make(map[string]*types.GraphData)
	for _, namespace := range namespaces {
		wg.Add(1)
		go func(namespace types.NameSpace) {
			defer wg.Done()
			graph, err := p.graphRepo.SearchNode(ctx, namespace, chatManage.Entity)
			if err != nil || graph == nil {
				return
			}
			mu.Lock()
			defer mu.Unlock()
			if graphs[namespace.KnowledgeBase] == nil {
				graphs[namespace.KnowledgeBase] = &types.GraphData{}
			}
			group := graphs[namespace.KnowledgeBase]
			group.Node = append(group.Node, graph.Node...)
			group.Relation = append(group.Relation, graph.Relation...)
		}(namespace)
	}
	wg.Wait()
	chatManage.GraphResult = &types.GraphData{}
	for kbID, graph := range graphs {
		kb := sources[kbID]
		if !p.canReadGraphSource(ctx, kb) {
			continue
		}
		chunkIDs := filterSeenChunk(ctx, graph, chatManage.SearchResult)
		if len(chunkIDs) == 0 {
			continue
		}
		requested := make(map[string]bool, len(chunkIDs))
		for _, id := range chunkIDs {
			requested[id] = true
		}
		sourceCtx := types.WithExecutionTenant(ctx, kb.TenantID)
		chunks, err := p.chunkRepo.ListChunksByID(sourceCtx, kb.TenantID, chunkIDs)
		if err != nil {
			continue
		}
		var validChunks []*types.Chunk
		var knowledgeIDs []string
		for _, chunk := range chunks {
			if chunk == nil || !requested[chunk.ID] || !chunk.IsEnabled || chunk.TenantID != kb.TenantID || chunk.KnowledgeBaseID != kbID || !graphTargetAllowsKnowledge(chatManage.SearchTargets, kb, chunk.KnowledgeID) {
				continue
			}
			validChunks = append(validChunks, chunk)
			knowledgeIDs = append(knowledgeIDs, chunk.KnowledgeID)
		}
		if len(validChunks) == 0 {
			continue
		}
		knowledges, err := p.knowledgeRepo.GetKnowledgeBatch(sourceCtx, kb.TenantID, knowledgeIDs)
		if err != nil {
			continue
		}
		knowledgeMap := make(map[string]*types.Knowledge)
		for _, knowledge := range knowledges {
			if knowledge != nil && knowledge.TenantID == kb.TenantID && knowledge.KnowledgeBaseID == kbID && graphTargetAllowsKnowledge(chatManage.SearchTargets, kb, knowledge.ID) {
				knowledgeMap[knowledge.ID] = knowledge
			}
		}
		var results []*types.SearchResult
		for _, chunk := range validChunks {
			if knowledge := knowledgeMap[chunk.KnowledgeID]; knowledge != nil {
				results = append(results, chunk2SearchResult(chunk, knowledge))
			}
		}
		searchutil.EnrichSearchResultsImageInfo(sourceCtx, p.chunkRepo, kb.TenantID, results)
		if !p.canReadGraphSource(ctx, kb) {
			continue
		}
		chatManage.GraphResult.Node = append(chatManage.GraphResult.Node, graph.Node...)
		chatManage.GraphResult.Relation = append(chatManage.GraphResult.Relation, graph.Relation...)
		chatManage.SearchResult = append(chatManage.SearchResult, results...)
	}
	chatManage.SearchResult = removeDuplicateResults(chatManage.SearchResult)
	return next()
}

func (p *PluginSearchEntity) canReadGraphSource(ctx context.Context, kb *types.KnowledgeBase) bool {
	if kb == nil {
		return false
	}
	var shares access.KBShareLookup
	if types.CallerFromContext(ctx).UserID != "" {
		shares = p.shares
	}
	allowed, err := access.NewKBPermissions(ctx, shares).Check(kb.ID, kb.TenantID, types.OrgRoleViewer)
	return err == nil && allowed
}

func graphTargetAllowsKnowledge(targets types.SearchTargets, kb *types.KnowledgeBase, knowledgeID string) bool {
	if kb == nil || knowledgeID == "" {
		return false
	}
	for _, target := range targets {
		if target == nil || target.KnowledgeBaseID != kb.ID || target.TenantID != kb.TenantID {
			continue
		}
		switch target.Type {
		case types.SearchTargetTypeKnowledgeBase:
			if len(target.TagIDs) == 0 && len(target.ScopeTagIDs) == 0 {
				return true
			}
		case types.SearchTargetTypeKnowledge:
			if slices.Contains(target.KnowledgeIDs, knowledgeID) {
				return true
			}
		}
	}
	return false
}

// filterSeenChunk filters seen chunks from the graph
func filterSeenChunk(ctx context.Context, graph *types.GraphData, searchResult []*types.SearchResult) []string {
	seen := map[string]bool{}
	for _, chunk := range searchResult {
		seen[chunk.ID] = true
	}
	logger.Infof(ctx, "filterSeenChunk: seen count: %d", len(seen))

	chunkIDs := []string{}
	for _, node := range graph.Node {
		for _, chunkID := range node.Chunks {
			if seen[chunkID] {
				continue
			}
			seen[chunkID] = true
			chunkIDs = append(chunkIDs, chunkID)
		}
	}
	logger.Infof(ctx, "filterSeenChunk: new chunkIDs count: %d", len(chunkIDs))
	return chunkIDs
}

// chunk2SearchResult converts a chunk to a search result
func chunk2SearchResult(chunk *types.Chunk, knowledge *types.Knowledge) *types.SearchResult {
	return &types.SearchResult{
		ID:                chunk.ID,
		Content:           chunk.Content,
		ContentRevision:   chunk.ContentRevision,
		KnowledgeID:       chunk.KnowledgeID,
		ChunkIndex:        chunk.ChunkIndex,
		KnowledgeTitle:    knowledge.Title,
		StartAt:           chunk.StartAt,
		EndAt:             chunk.EndAt,
		Seq:               chunk.ChunkIndex,
		Score:             1.0,
		MatchType:         types.MatchTypeGraph,
		Metadata:          knowledge.GetMetadata(),
		ChunkType:         string(chunk.ChunkType),
		ParentChunkID:     chunk.ParentChunkID,
		ImageInfo:         chunk.ImageInfo,
		KnowledgeFilename: knowledge.FileName,
		KnowledgeSource:   knowledge.Source,
		KnowledgeChannel:  knowledge.Channel,
		ChunkMetadata:     chunk.Metadata,
		KnowledgeBaseID:   knowledge.KnowledgeBaseID,
	}
}
