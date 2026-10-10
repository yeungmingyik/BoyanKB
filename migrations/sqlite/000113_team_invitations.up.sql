ALTER TABLE tenant_invitations ADD COLUMN team_manager_user_id VARCHAR(36) NOT NULL DEFAULT '';
CREATE INDEX idx_tenant_invitations_team_manager ON tenant_invitations (tenant_id, team_manager_user_id) WHERE team_manager_user_id <> '' AND deleted_at IS NULL;
