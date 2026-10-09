package businessworkflow

import (
	"os"
	"testing"
)

func TestSQLiteMigrationPreservesConfirmationConstraint(t *testing.T) {
	s, ctx, p := fixture(t)
	if err := s.DB.Migrator().DropTable(&Job{}); err != nil {
		t.Fatal(err)
	}
	migration, err := os.ReadFile("../../migrations/sqlite/000112_boyan_document_jobs.up.sql")
	if err != nil {
		t.Fatal(err)
	}
	if err := s.DB.Exec(string(migration)).Error; err != nil {
		t.Fatal(err)
	}
	job := proposed(t, s, ctx, p)
	if err := s.DB.Model(&Job{}).Where("id = ?", job.ID).Update("state", Published).Error; err == nil {
		t.Fatal("database accepted publication without confirmation")
	}
	if _, err := s.Confirm(ctx, "session", job.ID, job.OutlineHash); err != nil {
		t.Fatal(err)
	}
}
