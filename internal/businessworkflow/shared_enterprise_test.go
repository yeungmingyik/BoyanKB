package businessworkflow

import (
	"encoding/json"
	"testing"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/stretchr/testify/require"
)

func TestSharedEnterpriseWorkflowKeepsPrivateSessionsAndFilesWithoutPrivateKnowledge(t *testing.T) {
	for _, headquarters := range []bool{false, true} {
		t.Run(map[bool]string{false: "partner", true: "headquarters"}[headquarters], func(t *testing.T) {
			s, ctx, proposal := fixture(t)
			shares := &revisionShares{allowed: true}
			s.Shares = shares
			if !headquarters {
				for _, table := range []string{"knowledge_bases", "knowledges", "chunks"} {
					require.NoError(t, s.DB.Table(table).Where("tenant_id = ?", 1).Update("tenant_id", 2).Error)
				}
			}
			updateSkillProvision(t, s, func(job *types.PartnerProvisioningJob) {
				if headquarters {
					job.HQTenantID = job.TenantID
				}
				job.HQKnowledgeBaseIDs = []string{"kb"}
				job.State.KnowledgeMode = types.PartnerKnowledgeSharedEnterprise
				job.State.KnowledgeBaseID = ""
				job.State.ContributorUserID = ""
			})
			job := confirmed(t, s, ctx, proposal)
			published, err := s.Generate(ctx, "session", job.ID, job.OutlineHash)
			require.NoError(t, err)
			require.Equal(t, Published, published.State)
			file, err := s.Download(ctx, "session", job.ID)
			require.NoError(t, err)
			require.NotEmpty(t, file.Artifact)
			require.Equal(t, uint64(1), file.TenantID)
			require.Equal(t, "user", file.UserID)
			var payload Payload
			require.NoError(t, json.Unmarshal(file.Payload, &payload))
			require.Equal(t, "headquarters", payload.VerifiedSources[0].Authority)
			for _, caller := range []types.Caller{{TenantID: 3, UserID: "user", Role: types.TenantRoleViewer}, {TenantID: 1, UserID: "another-user", Role: types.TenantRoleViewer}} {
				other := types.WithCaller(ctx, caller)
				_, err = s.Download(other, "session", job.ID)
				require.ErrorIs(t, err, ErrDenied)
				_, err = s.List(other, "session")
				require.ErrorIs(t, err, ErrDenied)
			}
			_, err = s.Download(ctx, "second-session", job.ID)
			require.ErrorIs(t, err, ErrDenied)
			if !headquarters {
				shares.allowed = false
				_, err = s.Download(ctx, "session", job.ID)
				require.ErrorIs(t, err, ErrDenied)
				_, err = s.Propose(ctx, "session", proposal)
				require.ErrorIs(t, err, ErrDenied)
			}
		})
	}
}

func TestSharedEnterpriseWorkflowRejectsInvalidRegistryAndOtherKnowledge(t *testing.T) {
	for _, mutation := range []string{"private", "contributor", "multiple", "unknown", "other-source"} {
		t.Run(mutation, func(t *testing.T) {
			s, ctx, proposal := fixture(t)
			updateSkillProvision(t, s, func(job *types.PartnerProvisioningJob) {
				job.HQTenantID = job.TenantID
				job.HQKnowledgeBaseIDs = []string{"kb"}
				job.State.KnowledgeMode = types.PartnerKnowledgeSharedEnterprise
				job.State.KnowledgeBaseID = ""
				switch mutation {
				case "private":
					job.State.KnowledgeBaseID = "kb"
				case "contributor":
					job.State.ContributorUserID = job.OwnerUserID
				case "multiple":
					job.HQKnowledgeBaseIDs = append(job.HQKnowledgeBaseIDs, "another")
				case "unknown":
					job.State.KnowledgeMode = "all_tenants"
				case "other-source":
					job.HQKnowledgeBaseIDs = []string{"another"}
				}
			})
			_, err := s.Propose(ctx, "session", proposal)
			require.ErrorIs(t, err, ErrDenied)
		})
	}
}
