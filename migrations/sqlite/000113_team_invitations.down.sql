DROP INDEX IF EXISTS idx_tenant_invitations_team_manager;
ALTER TABLE tenant_invitations DROP COLUMN team_manager_user_id;
