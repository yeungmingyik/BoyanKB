package service

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"sort"
	"strings"
	"time"

	"github.com/Tencent/WeKnora/internal/models/chat"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
	"github.com/google/uuid"
	"github.com/hibiken/asynq"
	"github.com/neo4j/neo4j-go-driver/v6/neo4j"
	"github.com/redis/go-redis/v9"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

var (
	ErrGraphBackfillDenied      = errors.New("graph_backfill_denied")
	ErrGraphBackfillInvalid     = errors.New("graph_backfill_invalid")
	ErrGraphBackfillChanged     = errors.New("graph_backfill_source_changed")
	ErrGraphBackfillUnavailable = errors.New("graph_backfill_unavailable")
)

type graphBackfillInspector interface {
	GetTaskInfo(string, string) (*asynq.TaskInfo, error)
	RunTask(string, string) error
}

type GraphBackfillService struct {
	db         *gorm.DB
	queue      interfaces.TaskEnqueuer
	inspector  graphBackfillInspector
	checkGraph func(context.Context) error
	chatModel  func(context.Context, string) (chat.Chat, error)
}

func NewGraphBackfillService(db *gorm.DB, queue interfaces.TaskEnqueuer, redisClient *redis.Client, driver neo4j.Driver, models interfaces.ModelService) *GraphBackfillService {
	s := &GraphBackfillService{db: db, queue: queue}
	s.chatModel = func(ctx context.Context, id string) (chat.Chat, error) {
		model, err := models.GetModelByID(ctx, id)
		if err != nil || model == nil || model.Type != types.ModelTypeKnowledgeQA || model.Status != types.ModelStatusActive {
			return nil, ErrGraphBackfillUnavailable
		}
		return models.GetChatModel(ctx, id)
	}
	if redisClient != nil {
		s.inspector = asynq.NewInspectorFromRedisClient(redisClient)
	}
	s.checkGraph = func(ctx context.Context) error {
		if driver == nil {
			return ErrGraphBackfillUnavailable
		}
		ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
		defer cancel()
		return driver.VerifyAuthentication(ctx, nil)
	}
	return s
}

type GraphBackfillInput struct {
	KnowledgeIDs     []string `json:"knowledge_ids"`
	ExpectedPlanHash string   `json:"expected_plan_hash,omitempty"`
	RetryFailed      bool     `json:"retry_failed,omitempty"`
}

type GraphBackfillItem struct {
	KnowledgeID string `json:"knowledge_id"`
	ChunkID     string `json:"chunk_id"`
	TaskID      string `json:"task_id"`
	Status      string `json:"status"`
	RetryCount  int    `json:"retry_count"`
	payload     types.ExtractChunkPayload
}

type GraphBackfillPlan struct {
	KnowledgeBaseID string              `json:"knowledge_base_id"`
	ModelID         string              `json:"model_id"`
	PlanHash        string              `json:"plan_hash"`
	KnowledgeCount  int                 `json:"knowledge_count"`
	ChunkCount      int                 `json:"chunk_count"`
	RetentionDays   int                 `json:"retention_days"`
	Items           []GraphBackfillItem `json:"items"`
}

func graphBackfillHash(value any) string {
	data, _ := json.Marshal(value)
	hash := sha256.Sum256(data)
	return hex.EncodeToString(hash[:])
}

func graphBackfillIDs(ids []string) ([]string, error) {
	if len(ids) == 0 || len(ids) > 100 {
		return nil, ErrGraphBackfillInvalid
	}
	seen := map[string]bool{}
	result := append([]string(nil), ids...)
	for _, id := range result {
		if _, err := uuid.Parse(id); err != nil || seen[id] {
			return nil, ErrGraphBackfillInvalid
		}
		seen[id] = true
	}
	sort.Strings(result)
	return result, nil
}

func (s *GraphBackfillService) authorize(ctx context.Context, tenantID uint64, userID string) error {
	if s.db == nil || tenantID == 0 || userID == "" {
		return ErrGraphBackfillDenied
	}
	var count int64
	err := s.db.WithContext(ctx).Model(&types.TenantMember{}).
		Joins("JOIN users ON users.id = tenant_members.user_id AND users.deleted_at IS NULL AND users.is_active = ?", true).
		Joins("JOIN tenants ON tenants.id = tenant_members.tenant_id AND tenants.deleted_at IS NULL AND tenants.status = ?", "active").
		Where("tenant_members.tenant_id = ? AND tenant_members.user_id = ? AND tenant_members.status = ? AND tenant_members.role IN ?", tenantID, userID, types.TenantMemberStatusActive, []types.TenantRole{types.TenantRoleOwner, types.TenantRoleAdmin}).Count(&count).Error
	if err != nil {
		return ErrGraphBackfillUnavailable
	}
	if count != 1 {
		return ErrGraphBackfillDenied
	}
	return nil
}

func (s *GraphBackfillService) caller(ctx context.Context) (types.Caller, error) {
	c := types.CallerFromContext(ctx)
	p, ok := types.PrincipalFromContext(ctx)
	if !ok || p.Type != types.PrincipalWebUser || p.ID != c.UserID {
		return c, ErrGraphBackfillDenied
	}
	return c, s.authorize(ctx, c.TenantID, c.UserID)
}

func graphBackfillConfig(kb *types.KnowledgeBase, knowledge *types.Knowledge) (string, error) {
	if kb == nil || knowledge == nil || !kb.IsGraphEnabled() || kb.SummaryModelID == "" || knowledge.KnowledgeBaseID != kb.ID || knowledge.TenantID != kb.TenantID || knowledge.ParseStatus != types.ParseStatusCompleted || knowledge.PendingSubtasksCount != 0 {
		return "", ErrGraphBackfillChanged
	}
	overrides, err := knowledge.ProcessOverrides()
	if err != nil {
		return "", ErrGraphBackfillChanged
	}
	effective := ResolveProcessConfig(kb, overrides)
	if !effective.GraphEnabled {
		return "", ErrGraphBackfillChanged
	}
	return graphBackfillExtractHash(kb.SummaryModelID, effective.ExtractConfig), nil
}

func graphBackfillExtractHash(modelID string, config types.ExtractConfig) string {
	return graphBackfillHash(struct {
		ModelID string
		Config  types.ExtractConfig
	}{modelID, config})
}

func (s *GraphBackfillService) Plan(ctx context.Context, kbID string, input GraphBackfillInput) (*GraphBackfillPlan, error) {
	c, err := s.caller(ctx)
	if err != nil {
		return nil, err
	}
	ctx = types.WithExecutionTenant(ctx, c.TenantID)
	ids, err := graphBackfillIDs(input.KnowledgeIDs)
	if err != nil {
		return nil, err
	}
	if _, err := uuid.Parse(kbID); err != nil {
		return nil, ErrGraphBackfillInvalid
	}
	var kb types.KnowledgeBase
	if err := s.db.WithContext(ctx).Where("id = ? AND tenant_id = ?", kbID, c.TenantID).First(&kb).Error; err != nil {
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return nil, ErrGraphBackfillDenied
		}
		return nil, ErrGraphBackfillUnavailable
	}
	if !kb.IsGraphEnabled() || kb.SummaryModelID == "" {
		return nil, ErrGraphBackfillInvalid
	}
	if s.inspector == nil || s.checkGraph == nil || s.checkGraph(ctx) != nil || s.chatModel == nil {
		return nil, ErrGraphBackfillUnavailable
	}
	if model, err := s.chatModel(ctx, kb.SummaryModelID); err != nil || model == nil {
		return nil, ErrGraphBackfillUnavailable
	}
	var knowledge []*types.Knowledge
	if err := s.db.WithContext(ctx).Where("tenant_id = ? AND knowledge_base_id = ? AND id IN ?", c.TenantID, kbID, ids).Find(&knowledge).Error; err != nil {
		return nil, ErrGraphBackfillUnavailable
	}
	if len(knowledge) != len(ids) {
		return nil, ErrGraphBackfillDenied
	}
	configs := map[string]string{}
	for _, row := range knowledge {
		hash, err := graphBackfillConfig(&kb, row)
		if err != nil {
			return nil, err
		}
		configs[row.ID] = hash
	}
	var allChunks []*types.Chunk
	if err := s.db.WithContext(ctx).Where("tenant_id = ? AND knowledge_base_id = ? AND knowledge_id IN ? AND is_enabled = ? AND chunk_type IN ?", c.TenantID, kbID, ids, true, []string{types.ChunkTypeText, types.ChunkTypeImageOCR}).Order("id").Limit(501).Find(&allChunks).Error; err != nil {
		return nil, ErrGraphBackfillUnavailable
	}
	if len(allChunks) > 500 {
		return nil, ErrGraphBackfillInvalid
	}
	chunks := selectGraphChunks(allChunks)
	plan := &GraphBackfillPlan{KnowledgeBaseID: kbID, ModelID: kb.SummaryModelID, KnowledgeCount: len(ids), ChunkCount: len(chunks), RetentionDays: 30, Items: make([]GraphBackfillItem, 0, len(chunks))}
	bindings := make([]types.ExtractChunkPayload, 0, len(chunks))
	for _, chunk := range chunks {
		payload := types.ExtractChunkPayload{TenantID: c.TenantID, ChunkID: chunk.ID, ModelID: kb.SummaryModelID, KnowledgeID: chunk.KnowledgeID,
			GraphBackfill: &types.GraphBackfillBinding{KnowledgeBaseID: kbID, RequestedBy: c.UserID, ConfigHash: configs[chunk.KnowledgeID], ContentHash: graphBackfillHash(chunk.Content)}}
		item := GraphBackfillItem{KnowledgeID: chunk.KnowledgeID, ChunkID: chunk.ID, TaskID: "graph-backfill-" + graphBackfillHash(payload), payload: payload}
		if err := s.inspect(&item); err != nil {
			return nil, err
		}
		plan.Items = append(plan.Items, item)
		bindings = append(bindings, payload)
	}
	plan.PlanHash = graphBackfillHash(struct {
		KB       string
		IDs      []string
		Bindings []types.ExtractChunkPayload
	}{kbID, ids, bindings})
	return plan, nil
}

func (s *GraphBackfillService) inspect(item *GraphBackfillItem) error {
	info, err := s.inspector.GetTaskInfo(types.QueueGraph, item.TaskID)
	if errors.Is(err, asynq.ErrTaskNotFound) || errors.Is(err, asynq.ErrQueueNotFound) {
		item.Status = "not_submitted"
		return nil
	}
	if err != nil || info == nil {
		return ErrGraphBackfillUnavailable
	}
	item.Status = info.State.String()
	item.RetryCount = info.Retried
	return nil
}

func (s *GraphBackfillService) Submit(ctx context.Context, kbID string, input GraphBackfillInput) (*GraphBackfillPlan, error) {
	plan, err := s.Plan(ctx, kbID, input)
	if err != nil {
		return nil, err
	}
	if input.ExpectedPlanHash == "" || input.ExpectedPlanHash != plan.PlanHash {
		return nil, ErrGraphBackfillChanged
	}
	if s.queue == nil {
		return nil, ErrGraphBackfillUnavailable
	}
	for i := range plan.Items {
		item := &plan.Items[i]
		if err := s.ValidateTask(ctx, item.payload); err != nil {
			return nil, err
		}
		switch item.Status {
		case "not_submitted":
			_, err = enqueueChunkExtractTask(ctx, s.queue, item.payload, asynq.TaskID(item.TaskID), asynq.Retention(30*24*time.Hour))
			if err != nil && !errors.Is(err, asynq.ErrTaskIDConflict) {
				return nil, ErrGraphBackfillUnavailable
			}
		case "archived":
			if input.RetryFailed {
				if err := s.inspector.RunTask(types.QueueGraph, item.TaskID); err != nil {
					return nil, ErrGraphBackfillUnavailable
				}
			}
		}
		if err := s.inspect(item); err != nil {
			return nil, err
		}
	}
	return plan, nil
}

func (s *GraphBackfillService) ValidateTask(ctx context.Context, payload types.ExtractChunkPayload) error {
	binding := payload.GraphBackfill
	if binding == nil || binding.KnowledgeBaseID == "" || payload.KnowledgeID == "" || payload.Attempt != 0 {
		return ErrGraphBackfillInvalid
	}
	if err := s.authorize(ctx, payload.TenantID, binding.RequestedBy); err != nil {
		return err
	}
	var kb types.KnowledgeBase
	var knowledge types.Knowledge
	var chunk types.Chunk
	if err := s.db.WithContext(ctx).Where("id = ? AND tenant_id = ?", binding.KnowledgeBaseID, payload.TenantID).First(&kb).Error; err != nil {
		return graphBackfillReadError(err)
	}
	if err := s.db.WithContext(ctx).Where("id = ? AND tenant_id = ? AND knowledge_base_id = ?", payload.KnowledgeID, payload.TenantID, kb.ID).First(&knowledge).Error; err != nil {
		return graphBackfillReadError(err)
	}
	if err := s.db.WithContext(ctx).Where("id = ? AND tenant_id = ? AND knowledge_base_id = ? AND knowledge_id = ? AND is_enabled = ?", payload.ChunkID, payload.TenantID, kb.ID, knowledge.ID, true).First(&chunk).Error; err != nil {
		return graphBackfillReadError(err)
	}
	config, err := graphBackfillConfig(&kb, &knowledge)
	if err != nil || config != binding.ConfigHash || kb.SummaryModelID != payload.ModelID || graphBackfillHash(chunk.Content) != binding.ContentHash || len(selectGraphChunks([]*types.Chunk{&chunk})) != 1 || strings.TrimSpace(chunk.Content) == "" {
		return ErrGraphBackfillChanged
	}
	return nil
}

func graphBackfillReadError(err error) error {
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return ErrGraphBackfillChanged
	}
	return ErrGraphBackfillUnavailable
}

func (s *GraphBackfillService) WriteCurrentTask(ctx context.Context, payload types.ExtractChunkPayload, write func(context.Context) error) error {
	if payload.GraphBackfill == nil || write == nil {
		return ErrGraphBackfillInvalid
	}
	ctx, cancel := context.WithTimeout(ctx, 45*time.Second)
	defer cancel()
	return s.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		locks := []struct {
			table, condition string
			args             []any
		}{
			{"tenants", "id = ?", []any{payload.TenantID}},
			{"users", "id = ?", []any{payload.GraphBackfill.RequestedBy}},
			{"tenant_members", "tenant_id = ? AND user_id = ?", []any{payload.TenantID, payload.GraphBackfill.RequestedBy}},
			{"knowledge_bases", "id = ? AND tenant_id = ?", []any{payload.GraphBackfill.KnowledgeBaseID, payload.TenantID}},
			{"knowledges", "id = ? AND tenant_id = ?", []any{payload.KnowledgeID, payload.TenantID}},
			{"chunks", "id = ? AND tenant_id = ?", []any{payload.ChunkID, payload.TenantID}},
		}
		for _, lock := range locks {
			var rows []map[string]any
			if err := tx.Table(lock.table).Select("id").Where(lock.condition, lock.args...).Where("deleted_at IS NULL").Clauses(clause.Locking{Strength: "UPDATE"}).Find(&rows).Error; err != nil {
				return ErrGraphBackfillUnavailable
			}
			if len(rows) != 1 {
				return ErrGraphBackfillChanged
			}
		}
		current := *s
		current.db = tx
		if err := current.ValidateTask(ctx, payload); err != nil {
			return err
		}
		return write(ctx)
	})
}
