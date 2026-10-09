CREATE TABLE partner_provisioning_jobs (
    partner_key VARCHAR(64) PRIMARY KEY,
    request_hash VARCHAR(64) NOT NULL,
    spec_hash VARCHAR(64) NOT NULL,
    hq_tenant_id BIGINT NOT NULL REFERENCES tenants(id),
    hq_knowledge_base_ids JSONB NOT NULL,
    tenant_id BIGINT NOT NULL UNIQUE REFERENCES tenants(id),
    owner_user_id VARCHAR(36) NOT NULL REFERENCES users(id),
    status VARCHAR(32) NOT NULL,
    state JSONB NOT NULL DEFAULT '{}',
    revision BIGINT NOT NULL DEFAULT 0,
    lease_holder VARCHAR(36) NOT NULL DEFAULT '',
    lease_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
