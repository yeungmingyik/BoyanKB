package repository

import (
	"context"
	"errors"
	"testing"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/stretchr/testify/require"
)

func TestCatalogMutationUsesCurrentTenantPresetInEveryState(t *testing.T) {
	for _, status := range []string{"provisioning", "blocked", "ready"} {
		t.Run(status, func(t *testing.T) {
			repo, db := newSkillTestRepoWithDB(t)
			require.NoError(t, db.AutoMigrate(&types.PartnerProvisioningJob{}))
			state := types.PartnerProvisioningState{SkillCatalogIDs: map[string]string{"pdf": "cat-protected"}}
			require.NoError(t, db.Create(&types.PartnerProvisioningJob{PartnerKey: "own", TenantID: 7, Status: status, HQKnowledgeBaseIDs: types.StringArray{}, State: state}).Error)
			require.NoError(t, db.Create(&types.PartnerProvisioningJob{PartnerKey: "other", TenantID: 8, Status: "ready", HQKnowledgeBaseIDs: types.StringArray{}, State: types.PartnerProvisioningState{SkillCatalogIDs: map[string]string{"other": "foreign"}}}).Error)
			called := false
			require.NoError(t, repo.WithCatalogMutation(context.Background(), 7, func(_ TenantSkillRepository, got types.PartnerProvisioningState) error {
				called = true
				require.Equal(t, state, got)
				return nil
			}))
			require.True(t, called)
		})
	}
}

func TestCatalogMutationWithoutPresetAndRollback(t *testing.T) {
	repo, db := newSkillTestRepoWithDB(t)
	require.NoError(t, db.AutoMigrate(&types.PartnerProvisioningJob{}))
	failure := errors.New("cancel mutation")
	err := repo.WithCatalogMutation(context.Background(), 7, func(tx TenantSkillRepository, state types.PartnerProvisioningState) error {
		require.Empty(t, state.SkillCatalogIDs)
		require.Empty(t, state.RestrictedSkills)
		require.NoError(t, tx.CreateCatalog(context.Background(), &types.TenantSkillCatalogEntity{ID: "rolled-back", TenantID: 7, Name: "pdf"}))
		return failure
	})
	require.ErrorIs(t, err, failure)
	cat, err := repo.GetCatalog(context.Background(), 7, "rolled-back")
	require.NoError(t, err)
	require.Nil(t, cat)
}

func TestCatalogMutationLookupFailureDoesNotRunCallback(t *testing.T) {
	repo, _ := newSkillTestRepoWithDB(t)
	called := false
	err := repo.WithCatalogMutation(context.Background(), 7, func(_ TenantSkillRepository, _ types.PartnerProvisioningState) error {
		called = true
		return nil
	})
	require.Error(t, err)
	require.False(t, called)
}
