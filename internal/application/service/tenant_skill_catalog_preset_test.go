package service

import (
	"context"
	"errors"
	"strings"
	"testing"

	apperrors "github.com/Tencent/WeKnora/internal/errors"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/stretchr/testify/require"
)

func managedCatalogFixture(t *testing.T) (*installFixture, *types.TenantSkillCatalogEntity, []byte) {
	t.Helper()
	fx := newInstallFixture(t)
	archive := zipBundle(t, map[string]string{"SKILL.md": validSkillMD})
	cat, err := fx.svc.RegisterCatalogFromArchive(context.Background(), 7, archive)
	require.NoError(t, err)
	fx.skillRepo.presetStates = map[uint64]types.PartnerProvisioningState{7: {
		SkillCatalogIDs: map[string]string{cat.Name: cat.ID},
		RestrictedSkills: map[string]types.PartnerRestrictedSkill{cat.Name: {
			CatalogID: cat.ID, Version: cat.Version, CatalogSHA256: cat.BundleSHA256, Enabled: true,
		}},
	}}
	return fx, cat, archive
}

func requireManagedCatalogConflict(t *testing.T, err error) {
	t.Helper()
	require.Error(t, err)
	appErr, ok := apperrors.IsAppError(err)
	require.True(t, ok)
	require.Equal(t, 409, appErr.HTTPCode)
}

func TestManagedCatalogIdenticalRegistrationPreservesStorageAndIdentity(t *testing.T) {
	fx, cat, archive := managedCatalogFixture(t)
	saved := fx.savedBundles
	fx.saveErr = errors.New("unexpected storage write")
	got, err := fx.svc.RegisterCatalogFromArchive(context.Background(), 7, archive)
	require.NoError(t, err)
	require.Equal(t, cat, got)
	require.Equal(t, saved, fx.savedBundles)
	require.Empty(t, fx.deletedBundles)
}

func TestManagedCatalogRejectsDifferentArchiveBeforeStorage(t *testing.T) {
	fx, cat, _ := managedCatalogFixture(t)
	saved := fx.savedBundles
	archive := zipBundle(t, map[string]string{"SKILL.md": validSkillMD, "scripts/changed.py": "print('changed')\n"})
	_, err := fx.svc.RegisterCatalogFromArchive(context.Background(), 7, archive)
	requireManagedCatalogConflict(t, err)
	require.Equal(t, saved, fx.savedBundles)
	require.Empty(t, fx.deletedBundles)
	got, err := fx.skillRepo.GetCatalog(context.Background(), 7, cat.ID)
	require.NoError(t, err)
	require.Equal(t, cat, got)
}

func TestManagedCatalogRejectsDeleteFromEitherRegistrationIndex(t *testing.T) {
	for _, index := range []string{"catalog", "restricted", "catalog_id_only", "restricted_id_only"} {
		t.Run(index, func(t *testing.T) {
			fx, cat, _ := managedCatalogFixture(t)
			state := types.PartnerProvisioningState{}
			switch index {
			case "catalog":
				state.SkillCatalogIDs = map[string]string{cat.Name: cat.ID}
			case "restricted":
				state.RestrictedSkills = map[string]types.PartnerRestrictedSkill{cat.Name: {CatalogID: cat.ID}}
			case "catalog_id_only":
				state.SkillCatalogIDs = map[string]string{"previous-name": cat.ID}
			case "restricted_id_only":
				state.RestrictedSkills = map[string]types.PartnerRestrictedSkill{"previous-name": {CatalogID: cat.ID}}
			}
			fx.skillRepo.presetStates[7] = state
			requireManagedCatalogConflict(t, fx.svc.DeleteCatalog(context.Background(), 7, cat.ID))
			got, err := fx.skillRepo.GetCatalog(context.Background(), 7, cat.ID)
			require.NoError(t, err)
			require.Equal(t, cat, got)
			require.Empty(t, fx.deletedBundles)
		})
	}
}

func TestManagedCatalogProtectsRegisteredNameWithMissingOrDifferentID(t *testing.T) {
	for _, missing := range []bool{false, true} {
		t.Run(map[bool]string{false: "different_id", true: "missing_row"}[missing], func(t *testing.T) {
			fx, cat, archive := managedCatalogFixture(t)
			fx.skillRepo.presetStates[7] = types.PartnerProvisioningState{SkillCatalogIDs: map[string]string{cat.Name: "original-id"}}
			if missing {
				require.NoError(t, fx.skillRepo.DeleteCatalog(context.Background(), 7, cat.ID))
			}
			saved := fx.savedBundles
			_, err := fx.svc.RegisterCatalogFromArchive(context.Background(), 7, archive)
			requireManagedCatalogConflict(t, err)
			require.Equal(t, saved, fx.savedBundles)
		})
	}
}

func TestManagedCatalogRejectsInconsistentMetadataOrRegistrationDigest(t *testing.T) {
	for _, field := range []string{"version", "description", "instructions", "digest"} {
		t.Run(field, func(t *testing.T) {
			fx, cat, archive := managedCatalogFixture(t)
			switch field {
			case "version":
				cat.Version = "changed"
			case "description":
				cat.Description = "changed"
			case "instructions":
				cat.Instructions = "changed"
			case "digest":
				state := fx.skillRepo.presetStates[7]
				installed := state.RestrictedSkills[cat.Name]
				installed.CatalogSHA256 = strings.Repeat("f", 64)
				state.RestrictedSkills[cat.Name] = installed
			}
			require.NoError(t, fx.skillRepo.UpdateCatalog(context.Background(), cat))
			saved := fx.savedBundles
			_, err := fx.svc.RegisterCatalogFromArchive(context.Background(), 7, archive)
			requireManagedCatalogConflict(t, err)
			require.Equal(t, saved, fx.savedBundles)
		})
	}
}

func TestManagedCatalogRegistrationBeforeRestrictedInstallRemainsIdempotent(t *testing.T) {
	fx, cat, archive := managedCatalogFixture(t)
	fx.skillRepo.presetStates[7] = types.PartnerProvisioningState{SkillCatalogIDs: map[string]string{cat.Name: cat.ID}}
	got, err := fx.svc.RegisterCatalogFromArchive(context.Background(), 7, archive)
	require.NoError(t, err)
	require.Equal(t, cat, got)
}

func TestManagedCatalogPolicyLookupFailurePreventsMutation(t *testing.T) {
	fx, cat, archive := managedCatalogFixture(t)
	failure := errors.New("preset lookup unavailable")
	fx.skillRepo.presetErr = failure
	saved := fx.savedBundles
	_, err := fx.svc.RegisterCatalogFromArchive(context.Background(), 7, archive)
	require.ErrorIs(t, err, failure)
	require.ErrorIs(t, fx.svc.DeleteCatalog(context.Background(), 7, cat.ID), failure)
	require.Equal(t, saved, fx.savedBundles)
	require.Empty(t, fx.deletedBundles)
}

func TestManagedCatalogDoesNotReserveUnreferencedOrForeignTenantNames(t *testing.T) {
	fx, cat, _ := managedCatalogFixture(t)
	fx.skillRepo.presetStates[8] = fx.skillRepo.presetStates[7]
	fx.skillRepo.presetStates[7] = types.PartnerProvisioningState{SkillCatalogIDs: map[string]string{"another-skill": "another-id"}}
	archive := zipBundle(t, map[string]string{"SKILL.md": validSkillMD, "README.md": "Updated instructions"})
	got, err := fx.svc.RegisterCatalogFromArchive(context.Background(), 7, archive)
	require.NoError(t, err)
	require.Equal(t, cat.ID, got.ID)
	require.NotEqual(t, cat.BundleSHA256, got.BundleSHA256)
	require.NoError(t, fx.svc.DeleteCatalog(context.Background(), 7, cat.ID))
}
