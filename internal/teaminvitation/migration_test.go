package teaminvitation

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

func TestSQLiteTeamInvitationMigrationPreservesOwnerInvitations(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:"+uuid.NewString()+"?mode=memory&cache=shared"), &gorm.Config{})
	require.NoError(t, err)
	connection, err := db.DB()
	require.NoError(t, err)
	t.Cleanup(func() { connection.Close() })
	require.NoError(t, db.Exec("CREATE TABLE tenant_invitations(id INTEGER PRIMARY KEY,tenant_id INTEGER,token TEXT,deleted_at DATETIME)").Error)
	require.NoError(t, db.Exec("INSERT INTO tenant_invitations(id,tenant_id,token) VALUES (1,1,'owner-link')").Error)
	up, err := os.ReadFile(filepath.Join("..", "..", "migrations", "sqlite", "000113_team_invitations.up.sql"))
	require.NoError(t, err)
	require.NoError(t, db.Exec(string(up)).Error)
	var row struct{ Token, TeamManagerUserID string }
	require.NoError(t, db.Table("tenant_invitations").Where("id=1").Take(&row).Error)
	require.Equal(t, "owner-link", row.Token)
	require.Empty(t, row.TeamManagerUserID)
	down, err := os.ReadFile(filepath.Join("..", "..", "migrations", "sqlite", "000113_team_invitations.down.sql"))
	require.NoError(t, err)
	require.NoError(t, db.Exec(string(down)).Error)
	var count int64
	require.NoError(t, db.Table("tenant_invitations").Where("token = ?", "owner-link").Count(&count).Error)
	require.Equal(t, int64(1), count)
}
