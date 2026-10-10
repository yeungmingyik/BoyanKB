import argparse
import collections
import datetime
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PARTNER_KEYS = frozenset({'boyan-presets-demo-a', 'boyan-presets-demo-b'})
TEST_USERS = frozenset({'presets-20261009-a', 'presets-20261009-b'})
TEST_AGENTS = {
    '2b4890cf-21d8-42ab-9919-6b8b5670417c': {'local-smoke-rag'},
    '8a59838d-d859-4fc2-bda4-a43936fd1594': {'品牌验收（测试专用）', 'BoyanKB Branding Test'},
}
SMOKE_KB = '1b285a4d-6850-4d95-8773-c642e709f408'
PROBE_ID = '9e5b0d17-1622-4e29-85d6-3645a773fdae'
CANARY_IDS = frozenset({'71cf5133-8891-401f-8ed6-255491f77690', 'd063a190-a030-493c-aba4-600cd9eacc7f'})
QUERIES = {
    'tenants': 'SELECT id,name,status,created_at,deleted_at FROM tenants ORDER BY id',
    'users': "SELECT id,username,split_part(email,'@',2) AS email_domain,tenant_id,is_active,is_system_admin,deleted_at FROM users ORDER BY created_at",
    'members': 'SELECT id,user_id,tenant_id,role,status,deleted_at FROM tenant_members ORDER BY tenant_id,id',
    'invitations': 'SELECT id,tenant_id,invitee_user_id,role,status,expires_at,deleted_at FROM tenant_invitations ORDER BY tenant_id,id',
    'agents': "SELECT id,name,tenant_id,created_by,is_builtin,config->>'model_id' AS model_id,config->>'rerank_model_id' AS rerank_model_id,config->'knowledge_bases' AS knowledge_bases,deleted_at FROM custom_agents ORDER BY tenant_id,id",
    'knowledge_bases': "SELECT id,name,tenant_id,embedding_model_id,summary_model_id,description LIKE '%local-smoke%' AS smoke_marker,deleted_at FROM knowledge_bases ORDER BY tenant_id,id",
    'knowledges': "SELECT id,tenant_id,knowledge_base_id,title,parse_status,metadata->>'datasource_id' AS datasource_id,metadata->>'external_id' AS external_id,deleted_at FROM knowledges ORDER BY tenant_id,id",
    'sessions': "SELECT s.id,s.tenant_id,s.user_id,s.title,s.agent_id,s.agent_config->>'agent_id' AS config_agent_id,s.description='boyan-presets-isolated-acceptance' AS acceptance_marker,s.created_at,s.updated_at,s.deleted_at,(SELECT count(*) FROM messages m WHERE m.session_id=s.id) AS message_count FROM sessions s ORDER BY s.tenant_id,s.created_at",
    'document_jobs': 'SELECT id,tenant_id,user_id,session_id,state,scene,format,filename,artifact_sha256,octet_length(artifact) AS artifact_bytes,created_at FROM boyan_document_jobs ORDER BY tenant_id,created_at',
    'partner_jobs': "SELECT partner_key,tenant_id,hq_tenant_id,hq_knowledge_base_ids,owner_user_id,status,state->>'organization_id' AS organization_id,state->>'knowledge_base_id' AS knowledge_base_id,state->>'contributor_user_id' AS contributor_user_id,state->'agent_ids' AS agent_ids,state->'restricted_skills' AS restricted_skills FROM partner_provisioning_jobs ORDER BY tenant_id",
    'organizations': 'SELECT id,name,owner_id,owner_tenant_id,deleted_at FROM organizations ORDER BY id',
    'organization_members': 'SELECT id,organization_id,tenant_id,role FROM organization_tenant_members ORDER BY id',
    'kb_shares': 'SELECT id,knowledge_base_id,organization_id,source_tenant_id,permission,deleted_at FROM kb_shares ORDER BY id',
    'agent_shares': 'SELECT id,agent_id,organization_id,source_tenant_id,permission,deleted_at FROM agent_shares ORDER BY id',
    'data_sources': "SELECT id,tenant_id,knowledge_base_id,name,type,status,sync_mode,sync_deletions,config->'resource_ids' AS resource_ids,config->'settings'->>'allowed_space_id' AS allowed_space_id,deleted_at FROM data_sources ORDER BY id",
    'embed_channels': 'SELECT id,tenant_id,agent_id,name,enabled,deleted_at FROM embed_channels ORDER BY id',
    'models': 'SELECT id,tenant_id,name,display_name,type,source,is_builtin,managed_by,status,deleted_at FROM models ORDER BY id',
    'skill_catalog': 'SELECT id,tenant_id,name,version,bundle_sha256,deleted_at FROM tenant_skill_catalog ORDER BY tenant_id,name',
    'sandbox_policies': 'SELECT id,tenant_id,name,sandbox_type,deleted_at FROM tenant_sandbox_configs ORDER BY tenant_id,id',
    'storage_backends': 'SELECT id,tenant_id,name,provider,source,status,deleted_at FROM storage_backends ORDER BY tenant_id,id',
    'resources': 'SELECT id,tenant_id,original_name,kind,size,lifecycle,state,deleted_at FROM resources ORDER BY tenant_id,id',
    'resource_bindings': 'SELECT id,resource_id,tenant_id,owner_type,owner_id,relation FROM resource_bindings ORDER BY tenant_id,id',
    'other_counts': "SELECT 'messages' AS resource,count(*) AS total FROM messages UNION ALL SELECT 'chunks',count(*) FROM chunks UNION ALL SELECT 'embeddings',count(*) FROM embeddings UNION ALL SELECT 'api_keys',count(*) FROM tenant_api_keys UNION ALL SELECT 'native_skills',count(*) FROM tenant_skills UNION ALL SELECT 'skill_snapshots',count(*) FROM tenant_skill_snapshots UNION ALL SELECT 'memory_items',count(*) FROM memory_items UNION ALL SELECT 'im_channels',count(*) FROM im_channels UNION ALL SELECT 'organization_join_requests',count(*) FROM organization_join_requests UNION ALL SELECT 'message_artifacts',count(*) FROM message_artifacts",
}


class AuditError(Exception):
    pass


def validate_target(project, container):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,99}', project):
        raise AuditError('invalid_project')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', container):
        raise AuditError('invalid_postgres_container')


def checked_run(command, **kwargs):
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=60, **kwargs)
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        raise AuditError('docker_execution_failed') from None
    if result.returncode:
        raise AuditError('docker_execution_failed')
    return result.stdout


def read_database(project, container):
    validate_target(project, container)
    try:
        labels = json.loads(checked_run(['docker', 'inspect', container, '--format', '{{json .Config.Labels}}']))
    except (ValueError, TypeError):
        raise AuditError('invalid_container_metadata') from None
    if not isinstance(labels, dict) or labels.get('com.docker.compose.project') != project or labels.get('com.docker.compose.service') != 'postgres':
        raise AuditError('container_project_or_service_mismatch')
    items = ["'%s',(SELECT coalesce(json_agg(row_to_json(q)),'[]'::json) FROM (%s) q)" % (name, query) for name, query in QUERIES.items()]
    sql = "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY; SET LOCAL statement_timeout='30s'; SELECT json_build_object(" + ','.join(items) + '); ROLLBACK;'
    command = ['docker', 'exec', '-i', '-e', 'PGOPTIONS=-c default_transaction_read_only=on', container, 'sh', '-c', 'exec psql -X -q -A -t -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"']
    try:
        result = json.loads(checked_run(command, input=sql))
    except (ValueError, TypeError):
        raise AuditError('invalid_database_response') from None
    if not isinstance(result, dict) or set(result) != set(QUERIES) or any(not isinstance(value, list) for value in result.values()):
        raise AuditError('invalid_database_response')
    return result


def active(rows):
    return [row for row in rows if not row.get('deleted_at')]


def findings(data):
    partner_jobs = [row for row in data['partner_jobs'] if row['partner_key'] in PARTNER_KEYS]
    partner_tenants = {row['tenant_id'] for row in partner_jobs}
    test_users = [row for row in active(data['users']) if row['username'] in TEST_USERS and row['email_domain'] == 'example.invalid' and not row['is_system_admin']]
    default_tenants = {row['tenant_id'] for row in test_users}
    agents = [row for row in active(data['agents']) if row['id'] in TEST_AGENTS and row['name'] in TEST_AGENTS[row['id']] and not row['is_builtin']]
    agent_ids = {row['id'] for row in agents}
    channels = [row for row in active(data['embed_channels']) if row['agent_id'] in agent_ids and row['name'] == 'branding-verification-v1']
    channel_ids = {row['id'] for row in channels}
    marked = [row for row in active(data['sessions']) if row['acceptance_marker'] and row['tenant_id'] in partner_tenants]
    empty_embed = [row for row in active(data['sessions']) if row['message_count'] == 0 and any(row['user_id'] == 'embed_session:%s:%s:%s' % (row['tenant_id'], channel, row['id']) for channel in channel_ids)]
    strong_session_ids = {row['id'] for row in marked + empty_embed}
    review_sessions = [row for row in active(data['sessions']) if row['id'] not in strong_session_ids]
    smoke = [row for row in active(data['knowledge_bases']) if row['id'] == SMOKE_KB and row['name'] == 'local-smoke-stardust' and row['smoke_marker']]
    kb_ids = {row['id'] for row in smoke} | {row['knowledge_base_id'] for row in partner_jobs}
    org_ids = {row['organization_id'] for row in partner_jobs}
    jobs = [row for row in data['document_jobs'] if row['session_id'] in strong_session_ids]
    canaries = [row for row in active(data['knowledges']) if row['id'] in CANARY_IDS and row['tenant_id'] in partner_tenants]
    dependent_agents = [row for row in active(data['agents']) if row['tenant_id'] in partner_tenants]
    expected_agents = {value for job in partner_jobs for value in (job['agent_ids'] or {}).values()}
    extra_partner_agents = [row for row in dependent_agents if row['id'] not in expected_agents]
    tenant_ids = partner_tenants | default_tenants
    probes = [row['id'] for row in active(data['knowledges']) if row['id'] == PROBE_ID and row['external_id'] == 'JJ0zw4wfjiSGwSkypq3csTVJnCc']
    return {
        'status': 'REQUIRES_EXPLICIT_APPROVAL' if tenant_ids or agents or marked or smoke or probes else 'NO_STRONG_FIXTURE_FOUND',
        'retire_test_tenant_ids': sorted(tenant_ids),
        'test_user_ids': [row['id'] for row in test_users],
        'test_agent_ids': [row['id'] for row in agents + dependent_agents if row['id'] in agent_ids | expected_agents],
        'test_kb_ids': sorted(kb_ids),
        'test_knowledge_ids': [row['id'] for row in active(data['knowledges']) if row['knowledge_base_id'] in {kb['id'] for kb in smoke}] + [row['id'] for row in canaries],
        'test_organization_ids': sorted(org_ids),
        'organization_member_ids': [row['id'] for row in data['organization_members'] if row['organization_id'] in org_ids],
        'kb_share_ids': [row['id'] for row in active(data['kb_shares']) if row['organization_id'] in org_ids],
        'agent_share_ids': [row['id'] for row in active(data['agent_shares']) if row['organization_id'] in org_ids],
        'old_catalog_ids': [row['id'] for row in active(data['skill_catalog']) if row['tenant_id'] in partner_tenants],
        'retired_sandbox_policy_ids': [row['id'] for row in active(data['sandbox_policies']) if row['tenant_id'] in partner_tenants],
        'partner_keys': [row['partner_key'] for row in partner_jobs],
        'test_embed_channel_ids': sorted(channel_ids),
        'marked_acceptance_session_ids': [row['id'] for row in marked],
        'empty_test_embed_session_ids': [row['id'] for row in empty_embed],
        'conversation_content_review_required': [row['id'] for row in review_sessions],
        'unknown_agents_in_demo_workspaces': [row['id'] for row in extra_partner_agents],
        'document_job_ids': [row['id'] for row in jobs],
        'document_job_states': dict(collections.Counter(row['state'] for row in jobs)),
        'document_artifact_bytes': sum(row['artifact_bytes'] or 0 for row in jobs),
        'source_probe_hold_ids': probes,
        'protected_system_admin_ids': [row['id'] for row in active(data['users']) if row['is_system_admin']],
        'protected_hq_kb_ids': sorted({kb for row in partner_jobs for kb in row['hq_knowledge_base_ids']}),
        'protected_nonfixture_agent_ids': [row['id'] for row in active(data['agents']) if row['tenant_id'] not in tenant_ids and row['id'] not in agent_ids],
        'conditions': [
            'Only IDs are proposed. This program never applies changes.',
            'Install and verify HQ-owned three Agents and nine restricted Skills before retiring demo tenant bindings.',
            'Do not rename demo partner identities into real customer identities.',
            'Acceptance markers prove fixture origin, not that every later message was written by automation. Confirm the explicit session list and preserve wanted content.',
            'Nonempty unmarked conversations require separate review; never delete all sessions by tenant alone.',
            'Move or remove the Feishu probe at its authorized source before clearing its local import; preserve the 18 business documents and source configuration.',
            'Session soft deletion does not purge business document payloads or artifact bytes. Any hard purge requires an explicit separate scope.',
            'Default personal workspaces remain users.tenant_id dependencies until test account retirement is decided.',
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', default='weknora-local')
    parser.add_argument('--postgres-container', default='WeKnora-postgres')
    parser.add_argument('--report', type=Path, default=ROOT / 'temp/partner-delivery/audit-report.json')
    args = parser.parse_args(argv)
    try:
        report_path = args.report.resolve()
        if report_path.suffix.lower() != '.json' or report_path.exists():
            raise AuditError('report_requires_new_json_path')
        data = read_database(args.project, args.postgres_container)
        plan = findings(data)
        report = {'schema_version': 1, 'captured_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'project': args.project, 'postgres_container': args.postgres_container, 'read_only': True, 'counts': {key: len(value) for key, value in data.items()}, 'data': data, 'findings': plan, 'redacted': ['email local part', 'credentials', 'tokens', 'hashes of credentials', 'message and knowledge bodies', 'document payload and bytes', 'integration configuration']}
        report['counts'].update({row['resource']: row['total'] for row in data['other_counts']})
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open('x', encoding='utf-8') as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(json.dumps({'status': plan['status'], 'report': str(report_path), 'counts': report['counts'], 'candidate_counts': {key: len(value) for key, value in plan.items() if key.endswith('_ids') and isinstance(value, list)}}, ensure_ascii=True))
        return 2 if plan['status'] == 'REQUIRES_EXPLICIT_APPROVAL' else 0
    except (AuditError, OSError) as error:
        print(json.dumps({'status': 'ERROR', 'code': str(error) if isinstance(error, AuditError) else 'report_io_failed'}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
