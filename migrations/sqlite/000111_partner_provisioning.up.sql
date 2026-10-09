CREATE TABLE partner_provisioning_jobs (
    partner_key TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL,
    spec_hash TEXT NOT NULL,
    hq_tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    hq_knowledge_base_ids TEXT NOT NULL,
    tenant_id INTEGER NOT NULL UNIQUE REFERENCES tenants(id),
    owner_user_id TEXT NOT NULL REFERENCES users(id),
    status TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT '{}',
    revision INTEGER NOT NULL DEFAULT 0,
    lease_holder TEXT NOT NULL DEFAULT '',
    lease_until DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
