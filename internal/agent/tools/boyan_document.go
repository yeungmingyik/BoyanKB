package tools

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"

	"github.com/Tencent/WeKnora/internal/boyanskills"
	"github.com/Tencent/WeKnora/internal/businessworkflow"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
)

const ToolBoyanDocument = "boyan_document"

type BoyanDocumentTool struct {
	BaseTool
	service   *businessworkflow.Service
	sessionID string
	agentID   string
	targets   types.SearchTargets
	knowledge interfaces.KnowledgeService
	chunks    interfaces.ChunkService
}

func NewBoyanDocumentTool(service *businessworkflow.Service, sessionID, agentID string, targets types.SearchTargets, knowledge interfaces.KnowledgeService, chunks interfaces.ChunkService) *BoyanDocumentTool {
	return &BoyanDocumentTool{
		BaseTool: NewBaseTool(ToolBoyanDocument, "Read installed Boyan business Skills, propose a sourced document outline, check status, or generate a previously confirmed job. Call read_skill with skill_name for the scene and format first. Five scenes: courseware, event-plan, local-promotion-plan, cooperation-proposal, faq-guide. Formats: docx,pptx,xlsx,md. Propose only after required inputs are complete; sources must contain retrieved cN chunk handles. The user must explicitly confirm in the document panel. You cannot confirm jobs or bypass C review. Never claim a file exists before state=published. Changes require a new proposal and confirmation. Status returns the complete verified draft and source handles for revision; preserve unaffected sections and sources when proposing a replacement. Budget inputs belong only in budget.items with label, quantity, unit_price; the renderer appends a calculated budget table. Do not duplicate budget tables or add formulas, SUM placeholders, or quoted formulas in document.tables. Sources appendices are generated from verified server titles and chunk IDs; never invent or manually recreate source titles or IDs.", json.RawMessage(`{"type":"object","properties":{"operation":{"type":"string","enum":["read_skill","propose","status","generate"]},"skill_name":{"type":"string"},"job_id":{"type":"string"},"outline_hash":{"type":"string"},"scene":{"type":"string","enum":["courseware","event-plan","local-promotion-plan","cooperation-proposal","faq-guide"]},"format":{"type":"string","enum":["docx","pptx","xlsx","md"]},"outline_markdown":{"type":"string"},"requirements":{"type":"object","additionalProperties":{"type":"string"}},"document":{"type":"object","properties":{"title":{"type":"string"},"sections":{"type":"array","items":{"type":"object","properties":{"heading":{"type":"string"},"body":{"type":"string"},"bullets":{"type":"array","items":{"type":"string"}},"notes":{"type":"string"},"tables":{"type":"array","description":"Non-budget text tables only. No formulas or SUM placeholders; use budget.items for calculations.","items":{"type":"object","properties":{"headers":{"type":"array","items":{"type":"string"}},"rows":{"type":"array","items":{"type":"array","items":{"type":"string"}}}},"required":["headers","rows"],"additionalProperties":false}}},"required":["heading","body"],"additionalProperties":false}}},"required":["title","sections"],"additionalProperties":false},"sources":{"type":"array","description":"Retrieved chunk handles only. The server generates the source appendix with verified titles and IDs.","items":{"type":"object","properties":{"chunk_id":{"type":"string"}},"required":["chunk_id"],"additionalProperties":false}},"budget":{"type":"object","description":"Numeric assumptions for the automatically calculated budget table. Do not duplicate in document.tables or supply formulas.","properties":{"items":{"type":"array","items":{"type":"object","properties":{"label":{"type":"string"},"quantity":{"type":"number"},"unit_price":{"type":"number"}},"required":["label","quantity","unit_price"],"additionalProperties":false}}},"required":["items"],"additionalProperties":false}},"required":["operation"],"additionalProperties":false}`)),
		service:  service, sessionID: sessionID, agentID: agentID, targets: targets, knowledge: knowledge, chunks: chunks,
	}
}

func (t *BoyanDocumentTool) Execute(ctx context.Context, args json.RawMessage) (*types.ToolResult, error) {
	fail := func(err error) (*types.ToolResult, error) {
		if !errors.Is(err, businessworkflow.ErrDenied) && !errors.Is(err, businessworkflow.ErrConflict) && !errors.Is(err, businessworkflow.ErrInvalid) {
			err = businessworkflow.ErrUnavailable
		}
		return &types.ToolResult{Success: false, Error: err.Error()}, err
	}
	if t.service == nil || len(args) > 300*1024 {
		return fail(businessworkflow.ErrInvalid)
	}
	provision, err := t.service.Provision(ctx)
	if err != nil || provision.State.AgentIDs["planner"] != t.agentID {
		return fail(businessworkflow.ErrDenied)
	}
	var input struct {
		Operation   string `json:"operation"`
		SkillName   string `json:"skill_name"`
		JobID       string `json:"job_id"`
		OutlineHash string `json:"outline_hash"`
		businessworkflow.Proposal
	}
	decoder := json.NewDecoder(bytes.NewReader(args))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&input) != nil {
		return fail(businessworkflow.ErrInvalid)
	}
	var extra interface{}
	if decoder.Decode(&extra) != io.EOF {
		return fail(businessworkflow.ErrInvalid)
	}
	var job *businessworkflow.Job
	switch input.Operation {
	case "read_skill":
		guide, err := boyanskills.Read(ctx, t.service.DB, types.CallerFromContext(ctx).TenantID, input.SkillName)
		if err != nil {
			return fail(businessworkflow.ErrDenied)
		}
		return &types.ToolResult{Success: true, Output: guide, Data: map[string]interface{}{"skill_name": input.SkillName}}, nil
	case "propose":
		if _, err := boyanskills.Read(ctx, t.service.DB, provision.TenantID, "boyan-"+input.Scene); err != nil {
			return fail(businessworkflow.ErrDenied)
		}
		if _, err := boyanskills.Read(ctx, t.service.DB, provision.TenantID, "boyan-"+input.Format); err != nil {
			return fail(businessworkflow.ErrDenied)
		}
		for _, source := range input.Sources {
			if _, err := authorizeChunkInSearchTargets(ctx, t.targets, source.ChunkID, t.chunks, t.knowledge); err != nil {
				return fail(businessworkflow.ErrDenied)
			}
		}
		job, err = t.service.Propose(ctx, t.sessionID, input.Proposal)
	case "status":
		status, err := t.service.Revision(ctx, t.sessionID, input.JobID)
		if err != nil {
			return fail(err)
		}
		data, err := json.Marshal(status)
		if err != nil {
			return fail(err)
		}
		return &types.ToolResult{Success: true, Output: string(data), Data: status}, nil
	case "generate":
		job, err = t.service.Generate(ctx, t.sessionID, input.JobID, input.OutlineHash)
	default:
		return fail(businessworkflow.ErrInvalid)
	}
	if err != nil {
		return fail(err)
	}
	public := businessworkflow.Public(*job)
	data, _ := json.Marshal(public)
	return &types.ToolResult{Success: true, Output: string(data), Data: public}, nil
}
