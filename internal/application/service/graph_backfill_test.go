package service

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"

	"github.com/Tencent/WeKnora/internal/models/chat"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/alicebob/miniredis/v2"
	"github.com/hibiken/asynq"
	"github.com/redis/go-redis/v9"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

const graphTestKB = "00000000-0000-4000-8000-000000000001"
const graphTestKnowledge = "00000000-0000-4000-8000-000000000002"
const graphTestChunk = "00000000-0000-4000-8000-000000000003"

type graphTestChatBase = chat.Chat
type graphTestChat struct{ graphTestChatBase }

func graphBackfillFixture(t *testing.T) (*GraphBackfillService, context.Context, *asynq.Inspector) {
	t.Helper()
	t.Setenv("NEO4J_ENABLE", "true")
	db, err := gorm.Open(sqlite.Open("file:"+t.Name()+"?mode=memory&cache=shared"), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	for _, sql := range []string{
		"CREATE TABLE users (id TEXT PRIMARY KEY, is_active BOOLEAN, deleted_at DATETIME)",
		"CREATE TABLE tenants (id INTEGER PRIMARY KEY, status TEXT, deleted_at DATETIME)",
		"CREATE TABLE tenant_members (id INTEGER PRIMARY KEY, tenant_id INTEGER, user_id TEXT, role TEXT, status TEXT, deleted_at DATETIME)",
		"CREATE TABLE knowledge_bases (id TEXT PRIMARY KEY, tenant_id INTEGER, summary_model_id TEXT, indexing_strategy TEXT, extract_config TEXT, deleted_at DATETIME)",
		"CREATE TABLE knowledges (id TEXT PRIMARY KEY, tenant_id INTEGER, knowledge_base_id TEXT, parse_status TEXT, pending_subtasks_count INTEGER, metadata TEXT, deleted_at DATETIME)",
		"CREATE TABLE chunks (id TEXT PRIMARY KEY, tenant_id INTEGER, knowledge_base_id TEXT, knowledge_id TEXT, content TEXT, chunk_type TEXT, is_enabled BOOLEAN, deleted_at DATETIME)",
		"INSERT INTO users (id,is_active) VALUES ('owner',true)",
		"INSERT INTO tenants (id,status) VALUES (1,'active'),(2,'active')",
		"INSERT INTO tenant_members (tenant_id,user_id,role,status) VALUES (1,'owner','owner','active')",
	} {
		require.NoError(t, db.Exec(sql).Error)
	}
	require.NoError(t, db.Exec("INSERT INTO knowledge_bases (id,tenant_id,summary_model_id,indexing_strategy,extract_config) VALUES (?,1,'chat',?,?)", graphTestKB, []byte(`{"vector_enabled":true,"keyword_enabled":true,"graph_enabled":true}`), []byte(`{"enabled":true}`)).Error)
	require.NoError(t, db.Exec("INSERT INTO knowledges (id,tenant_id,knowledge_base_id,parse_status,pending_subtasks_count,metadata) VALUES (?,1,?,'completed',0,'{}')", graphTestKnowledge, graphTestKB).Error)
	require.NoError(t, db.Exec("INSERT INTO chunks (id,tenant_id,knowledge_base_id,knowledge_id,content,chunk_type,is_enabled) VALUES (?,1,?,?,?,'text',true)", graphTestChunk, graphTestKB, graphTestKnowledge, "上海分部负责课程交付。").Error)
	server := miniredis.RunT(t)
	redisClient := redis.NewClient(&redis.Options{Addr: server.Addr()})
	client := asynq.NewClientFromRedisClient(redisClient)
	inspector := asynq.NewInspectorFromRedisClient(redisClient)
	t.Cleanup(func() { _ = redisClient.Close(); sqlDB, _ := db.DB(); _ = sqlDB.Close() })
	s := &GraphBackfillService{db: db, queue: client, inspector: inspector, checkGraph: func(context.Context) error { return nil }, chatModel: func(context.Context, string) (chat.Chat, error) { return &graphTestChat{}, nil }}
	ctx := types.WithCaller(context.Background(), types.Caller{TenantID: 1, UserID: "owner", Role: types.TenantRoleOwner})
	ctx = types.WithPrincipal(ctx, types.Principal{Type: types.PrincipalWebUser, ID: "owner"})
	return s, ctx, inspector
}

func TestGraphBackfillPlanAndRepeatDoNotMutateKnowledge(t *testing.T) {
	s, ctx, inspector := graphBackfillFixture(t)
	input := GraphBackfillInput{KnowledgeIDs: []string{graphTestKnowledge}}
	plan, err := s.Plan(ctx, graphTestKB, input)
	require.NoError(t, err)
	require.Len(t, plan.Items, 1)
	require.Equal(t, "not_submitted", plan.Items[0].Status)
	input.ExpectedPlanHash = plan.PlanHash
	for i := 0; i < 2; i++ {
		result, err := s.Submit(ctx, graphTestKB, input)
		require.NoError(t, err)
		require.Equal(t, plan.PlanHash, result.PlanHash)
		require.Equal(t, "pending", result.Items[0].Status)
	}
	items, err := inspector.ListPendingTasks(types.QueueGraph)
	require.NoError(t, err)
	require.Len(t, items, 1)
	var payload types.ExtractChunkPayload
	require.NoError(t, json.Unmarshal(items[0].Payload, &payload))
	require.Equal(t, graphTestKnowledge, payload.KnowledgeID)
	require.Equal(t, "owner", payload.GraphBackfill.RequestedBy)
	require.Zero(t, payload.Attempt)
	var knowledge types.Knowledge
	var chunk types.Chunk
	require.NoError(t, s.db.First(&knowledge, "id = ?", graphTestKnowledge).Error)
	require.NoError(t, s.db.First(&chunk, "id = ?", graphTestChunk).Error)
	require.Equal(t, types.ParseStatusCompleted, knowledge.ParseStatus)
	require.Zero(t, knowledge.PendingSubtasksCount)
	require.Equal(t, "上海分部负责课程交付。", chunk.Content)
}

func TestGraphBackfillRejectsViewerAPIKeyAndForeignWorkspace(t *testing.T) {
	s, ctx, _ := graphBackfillFixture(t)
	input := GraphBackfillInput{KnowledgeIDs: []string{graphTestKnowledge}}
	apiContext := types.WithPrincipal(ctx, types.Principal{Type: types.PrincipalAPITenant, ID: "key"})
	_, err := s.Plan(apiContext, graphTestKB, input)
	require.ErrorIs(t, err, ErrGraphBackfillDenied)
	require.NoError(t, s.db.Exec("UPDATE knowledge_bases SET tenant_id=2").Error)
	_, err = s.Plan(ctx, graphTestKB, input)
	require.ErrorIs(t, err, ErrGraphBackfillDenied)
	require.NoError(t, s.db.Exec("UPDATE knowledge_bases SET tenant_id=1").Error)
	require.NoError(t, s.db.Exec("UPDATE tenant_members SET role='viewer'").Error)
	_, err = s.Plan(ctx, graphTestKB, input)
	require.ErrorIs(t, err, ErrGraphBackfillDenied)
}

func TestGraphBackfillRejectsStalePlanAndSourceChanges(t *testing.T) {
	s, ctx, inspector := graphBackfillFixture(t)
	input := GraphBackfillInput{KnowledgeIDs: []string{graphTestKnowledge}}
	plan, err := s.Plan(ctx, graphTestKB, input)
	require.NoError(t, err)
	input.ExpectedPlanHash = plan.PlanHash
	require.NoError(t, s.db.Exec("UPDATE chunks SET content='新的正文'").Error)
	_, err = s.Submit(ctx, graphTestKB, input)
	require.ErrorIs(t, err, ErrGraphBackfillChanged)
	require.ErrorIs(t, s.ValidateTask(ctx, plan.Items[0].payload), ErrGraphBackfillChanged)
	_, err = inspector.GetTaskInfo(types.QueueGraph, plan.Items[0].TaskID)
	require.Error(t, err)
}

func TestGraphBackfillWorkerRechecksPermissionBeforeExtraction(t *testing.T) {
	s, ctx, _ := graphBackfillFixture(t)
	plan, err := s.Plan(ctx, graphTestKB, GraphBackfillInput{KnowledgeIDs: []string{graphTestKnowledge}})
	require.NoError(t, err)
	require.NoError(t, s.ValidateTask(ctx, plan.Items[0].payload))
	require.NoError(t, s.db.Exec("UPDATE tenant_members SET status='suspended'").Error)
	payload, err := json.Marshal(plan.Items[0].payload)
	require.NoError(t, err)
	worker := &ChunkExtractService{graphBackfill: s}
	err = worker.Handle(context.Background(), asynq.NewTask(types.TypeChunkExtract, payload))
	require.ErrorIs(t, err, ErrGraphBackfillDenied)
	require.ErrorIs(t, err, asynq.SkipRetry)
}

func TestGraphBackfillWorkerRetriesTransientValidationFailures(t *testing.T) {
	for _, table := range []string{"tenant_members", "knowledge_bases", "knowledges", "chunks"} {
		t.Run(table, func(t *testing.T) {
			s, ctx, _ := graphBackfillFixture(t)
			plan, err := s.Plan(ctx, graphTestKB, GraphBackfillInput{KnowledgeIDs: []string{graphTestKnowledge}})
			require.NoError(t, err)
			payload, err := json.Marshal(plan.Items[0].payload)
			require.NoError(t, err)
			require.NoError(t, s.db.Callback().Query().Before("gorm:query").Register("test:read_failure", func(tx *gorm.DB) {
				if tx.Statement.Table == table {
					tx.AddError(fmt.Errorf("database connection unavailable"))
				}
			}))
			require.ErrorIs(t, s.ValidateTask(ctx, plan.Items[0].payload), ErrGraphBackfillUnavailable)
			worker := &ChunkExtractService{graphBackfill: s}
			err = worker.Handle(context.Background(), asynq.NewTask(types.TypeChunkExtract, payload))
			require.ErrorIs(t, err, ErrGraphBackfillUnavailable)
			require.NotErrorIs(t, err, asynq.SkipRetry)
			require.NoError(t, s.db.Callback().Query().Remove("test:read_failure"))
			require.NoError(t, s.ValidateTask(ctx, plan.Items[0].payload))
		})
	}
}

func TestGraphBackfillWorkerDoesNotRetryMissingOrChangedSources(t *testing.T) {
	for _, update := range []string{
		"UPDATE knowledge_bases SET deleted_at=CURRENT_TIMESTAMP",
		"UPDATE knowledges SET deleted_at=CURRENT_TIMESTAMP",
		"UPDATE chunks SET deleted_at=CURRENT_TIMESTAMP",
		"UPDATE chunks SET content='变更后的正文'",
	} {
		t.Run(graphBackfillHash(update)[:8], func(t *testing.T) {
			s, ctx, _ := graphBackfillFixture(t)
			plan, err := s.Plan(ctx, graphTestKB, GraphBackfillInput{KnowledgeIDs: []string{graphTestKnowledge}})
			require.NoError(t, err)
			require.NoError(t, s.db.Exec(update).Error)
			payload, err := json.Marshal(plan.Items[0].payload)
			require.NoError(t, err)
			worker := &ChunkExtractService{graphBackfill: s}
			err = worker.Handle(context.Background(), asynq.NewTask(types.TypeChunkExtract, payload))
			require.ErrorIs(t, err, ErrGraphBackfillChanged)
			require.ErrorIs(t, err, asynq.SkipRetry)
		})
	}
}

func TestGraphBackfillWorkerRejectsInvalidBindingWithoutRetry(t *testing.T) {
	s, ctx, _ := graphBackfillFixture(t)
	plan, err := s.Plan(ctx, graphTestKB, GraphBackfillInput{KnowledgeIDs: []string{graphTestKnowledge}})
	require.NoError(t, err)
	plan.Items[0].payload.Attempt = 1
	payload, err := json.Marshal(plan.Items[0].payload)
	require.NoError(t, err)
	worker := &ChunkExtractService{graphBackfill: s}
	err = worker.Handle(context.Background(), asynq.NewTask(types.TypeChunkExtract, payload))
	require.ErrorIs(t, err, ErrGraphBackfillInvalid)
	require.ErrorIs(t, err, asynq.SkipRetry)
}

func TestGraphBackfillRejectsDisabledBusyAndMissingResources(t *testing.T) {
	for _, update := range []string{
		"UPDATE knowledge_bases SET extract_config='{\"enabled\":false}'",
		"UPDATE knowledges SET parse_status='processing'",
		"UPDATE knowledges SET pending_subtasks_count=1",
		"UPDATE knowledges SET tenant_id=2",
	} {
		t.Run(fmt.Sprintf("case-%x", graphBackfillHash(update)[:8]), func(t *testing.T) {
			s, ctx, _ := graphBackfillFixture(t)
			require.NoError(t, s.db.Exec(update).Error)
			_, err := s.Plan(ctx, graphTestKB, GraphBackfillInput{KnowledgeIDs: []string{graphTestKnowledge}})
			require.Error(t, err)
		})
	}
}

func TestGraphBackfillRequiresGraphAndChatModelReadiness(t *testing.T) {
	s, ctx, _ := graphBackfillFixture(t)
	s.checkGraph = func(context.Context) error { return fmt.Errorf("offline") }
	_, err := s.Plan(ctx, graphTestKB, GraphBackfillInput{KnowledgeIDs: []string{graphTestKnowledge}})
	require.ErrorIs(t, err, ErrGraphBackfillUnavailable)
	s.checkGraph = func(context.Context) error { return nil }
	s.chatModel = func(context.Context, string) (chat.Chat, error) { return nil, nil }
	_, err = s.Plan(ctx, graphTestKB, GraphBackfillInput{KnowledgeIDs: []string{graphTestKnowledge}})
	require.ErrorIs(t, err, ErrGraphBackfillUnavailable)
}

func TestGraphBackfillWriteRevalidatesSourceAndAuthorization(t *testing.T) {
	s, ctx, _ := graphBackfillFixture(t)
	plan, err := s.Plan(ctx, graphTestKB, GraphBackfillInput{KnowledgeIDs: []string{graphTestKnowledge}})
	require.NoError(t, err)
	calls := 0
	write := func(context.Context) error { calls++; return nil }
	require.NoError(t, s.WriteCurrentTask(ctx, plan.Items[0].payload, write))
	require.Equal(t, 1, calls)
	require.NoError(t, s.db.Exec("UPDATE tenant_members SET role='viewer'").Error)
	require.ErrorIs(t, s.WriteCurrentTask(ctx, plan.Items[0].payload, write), ErrGraphBackfillDenied)
	require.NoError(t, s.db.Exec("UPDATE tenant_members SET role='owner'").Error)
	require.NoError(t, s.db.Exec("UPDATE chunks SET content='变更后的正文'").Error)
	require.ErrorIs(t, s.WriteCurrentTask(ctx, plan.Items[0].payload, write), ErrGraphBackfillChanged)
	require.Equal(t, 1, calls)
}

func TestGraphBackfillArchivedRetryRequiresExplicitOption(t *testing.T) {
	s, ctx, inspector := graphBackfillFixture(t)
	input := GraphBackfillInput{KnowledgeIDs: []string{graphTestKnowledge}}
	plan, err := s.Plan(ctx, graphTestKB, input)
	require.NoError(t, err)
	input.ExpectedPlanHash = plan.PlanHash
	_, err = s.Submit(ctx, graphTestKB, input)
	require.NoError(t, err)
	require.NoError(t, inspector.ArchiveTask(types.QueueGraph, plan.Items[0].TaskID))
	result, err := s.Submit(ctx, graphTestKB, input)
	require.NoError(t, err)
	require.Equal(t, "archived", result.Items[0].Status)
	input.RetryFailed = true
	result, err = s.Submit(ctx, graphTestKB, input)
	require.NoError(t, err)
	require.Equal(t, "pending", result.Items[0].Status)
	items, err := inspector.ListPendingTasks(types.QueueGraph)
	require.NoError(t, err)
	require.Len(t, items, 1)
}
