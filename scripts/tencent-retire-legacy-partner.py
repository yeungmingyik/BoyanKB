import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess


ROOT = Path('/opt/boyankb')
TENANT = 10005
ORG = '3e30605d-8e24-44ea-92aa-e23c42455a60'
REPORT = ROOT / 'legacy-partner-retirement.json'


def helper():
    spec = importlib.util.spec_from_file_location('accounts', ROOT / 'scripts/tencent-accounts.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.host_guard()
    return module


def inventory_sql(a):
    queries = {
        'tenant': 'SELECT id,name,status,deleted_at FROM tenants WHERE id=10005',
        'organization': "SELECT id,name,owner_tenant_id,deleted_at FROM organizations WHERE id='" + ORG + "'",
        'shares': "SELECT id,knowledge_base_id,source_tenant_id,deleted_at FROM kb_shares WHERE organization_id='" + ORG + "'",
        'org_members': "SELECT tenant_id,role FROM organization_tenant_members WHERE organization_id='" + ORG + "'",
        'counts': "SELECT (SELECT count(*) FROM tenant_members WHERE tenant_id=10005 AND deleted_at IS NULL) AS members,(SELECT count(*) FROM users WHERE tenant_id=10005 AND deleted_at IS NULL) AS users,(SELECT count(*) FROM knowledge_bases WHERE tenant_id=10005 AND deleted_at IS NULL) AS kbs,(SELECT count(*) FROM tenant_api_keys WHERE tenant_id=10005 AND revoked_at IS NULL) AS keys,(SELECT count(*) FROM tenant_invitations WHERE tenant_id=10005 AND deleted_at IS NULL AND status='pending') AS invitations,(SELECT count(*) FROM im_channels WHERE tenant_id=10005 AND deleted_at IS NULL AND enabled) AS im_channels,(SELECT count(*) FROM embed_channels WHERE tenant_id=10005 AND deleted_at IS NULL AND enabled) AS embed_channels,(SELECT count(*) FROM boyan_document_jobs WHERE tenant_id=10005 AND state IN ('approved','generating','reviewing')) AS document_jobs,(SELECT count(*) FROM agent_shares WHERE organization_id='" + ORG + "' AND deleted_at IS NULL) AS agent_shares,(SELECT count(*) FROM partner_provisioning_jobs WHERE tenant_id=10005 AND (lease_holder<>'' OR lease_until IS NOT NULL)) AS leases",
        'archived_assets': "SELECT (SELECT count(*) FROM custom_agents WHERE tenant_id=10005 AND deleted_at IS NULL AND NOT is_builtin) AS agents,(SELECT count(*) FROM tenant_skill_catalog WHERE tenant_id=10005 AND deleted_at IS NULL) AS skills,(SELECT count(*) FROM sessions WHERE tenant_id=10005 AND deleted_at IS NULL) AS sessions",
    }
    pairs = [a.sql_string(key) + ',' + a.rows_sql(query) for key, query in queries.items()]
    pairs.append("'protected'," + a.protected_sql())
    return 'jsonb_build_object(' + ','.join(pairs) + ')'


def inventory(a, postgres):
    return a.database(postgres, 'SELECT ' + inventory_sql(a) + ';')


def validate(a, data):
    a.require(len(data['tenant']) == 1, 'exact_legacy_tenant_required')
    tenant = data['tenant'][0]
    a.require(tenant['id'] == TENANT and tenant['name'] == '星辰教育有限公司', 'legacy_tenant_mismatch')
    a.require(len(data['organization']) == 1, 'exact_legacy_organization_required')
    org = data['organization'][0]
    a.require(org['name'] == 'boyan-partner-xingchen-education' and org['owner_tenant_id'] == 10000, 'legacy_organization_mismatch')
    a.require(all(value == 0 for value in data['counts'][0].values()), 'legacy_tenant_in_use')
    a.require(data['org_members'] == [{'role': 'admin', 'tenant_id': 10000}, {'role': 'viewer', 'tenant_id': 10005}], 'unexpected_organization_members')
    a.require(len(data['shares']) == 1 and data['shares'][0]['knowledge_base_id'] == a.KB and data['shares'][0]['source_tenant_id'] == 10000, 'unexpected_shared_resources')
    a.require(data['archived_assets'] == [{'agents': 3, 'sessions': 4, 'skills': 9}], 'legacy_assets_changed')
    states = [bool(tenant['deleted_at']), bool(org['deleted_at']), bool(data['shares'][0]['deleted_at'])]
    a.require(all(states) or not any(states), 'partial_retirement_requires_review')
    return all(states)


def backup(a, postgres):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = ROOT / 'backups' / ('pre-legacy-partner-' + stamp + '.dump')
    a.private_path(path, False)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
        result = subprocess.run(['docker', '--host', 'unix:///var/run/docker.sock', 'exec', postgres, 'sh', '-c', 'exec pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"'], stdout=stream, stderr=subprocess.PIPE, timeout=180, check=False)
        stream.flush()
        os.fsync(stream.fileno())
    a.require(result.returncode == 0, 'backup_failed')
    with path.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    a.verify_backup(path, checksum, postgres)
    return {'path': str(path), 'sha256': checksum}


def retire(a, postgres, before, commit=True):
    sql = """BEGIN;
SET LOCAL lock_timeout='10s';
SET LOCAL statement_timeout='30s';
LOCK TABLE tenants,tenant_members,users,organizations,organization_tenant_members,kb_shares,agent_shares,tenant_api_keys,tenant_invitations,im_channels,embed_channels,boyan_document_jobs,partner_provisioning_jobs,knowledge_bases,knowledges,chunks,embeddings,data_sources,custom_agents,tenant_skill_catalog,models,storage_backends,sessions,messages,resources,resource_bindings IN SHARE ROW EXCLUSIVE MODE;
DO $body$
BEGIN
    IF """ + inventory_sql(a) + ' IS DISTINCT FROM ' + a.json_sql(before) + """ THEN
        RAISE EXCEPTION 'state_changed_before_retirement';
    END IF;
END
$body$;
DO $body$
DECLARE affected integer;
BEGIN
    UPDATE kb_shares SET deleted_at=transaction_timestamp() WHERE id='9c3c3e05-e207-4228-b5da-a69fa96fda72' AND organization_id='3e30605d-8e24-44ea-92aa-e23c42455a60' AND knowledge_base_id='d9285ed1-0760-474b-ba82-a13347598176' AND source_tenant_id=10000 AND deleted_at IS NULL;
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected<>1 THEN RAISE EXCEPTION 'share_update_mismatch'; END IF;
    UPDATE organizations SET deleted_at=transaction_timestamp() WHERE id='3e30605d-8e24-44ea-92aa-e23c42455a60' AND owner_tenant_id=10000 AND deleted_at IS NULL;
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected<>1 THEN RAISE EXCEPTION 'organization_update_mismatch'; END IF;
    UPDATE tenants SET deleted_at=transaction_timestamp() WHERE id=10005 AND name='星辰教育有限公司' AND deleted_at IS NULL;
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected<>1 THEN RAISE EXCEPTION 'tenant_update_mismatch'; END IF;
    IF """ + a.protected_sql() + ' IS DISTINCT FROM ' + a.json_sql(before['protected']) + """ THEN
        RAISE EXCEPTION 'protected_resources_changed';
    END IF;
END
$body$;
""" + ('COMMIT;' if commit else 'ROLLBACK;') + """
SELECT jsonb_build_object('transaction_completed',true);
"""
    return a.database(postgres, sql)


def verify_api(a, address):
    values = a.credentials(ROOT / '.env')
    client = a.Client(address)
    results = []
    for email, key in zip(a.NEW_EMAILS, ('ADMIN_PASSWORD', 'MEMBER_PASSWORD')):
        token = client.login(email, values[key])
        me = client.request('GET', '/api/v1/auth/me', token=token)['data']
        a.require(me['user']['email'] == email and {row['tenant_id'] for row in me['memberships']} == {10000}, 'current_account_scope_changed')
        orgs = client.request('GET', '/api/v1/organizations', token=token)['data']['organizations']
        a.require(not any(row['id'] == ORG for row in orgs), 'retired_organization_visible')
        results.append({'email': email, 'headquarters_only': True, 'legacy_organization_absent': True})
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    a = helper()
    ids, address = a.containers()
    before = inventory(a, ids['postgres'])
    retired = validate(a, before)
    if not args.apply:
        print(json.dumps({'mode': 'audit', 'already_retired': retired, 'inventory': before}, ensure_ascii=False))
        return
    record = a.load_private(REPORT) if REPORT.exists() else None
    if retired and record and record.get('phase') == 'verified':
        print(json.dumps({'already_retired': True, 'api_checks': verify_api(a, address)}, ensure_ascii=False))
        return
    if record:
        a.require(record.get('phase') in ('backed_up', 'retired'), 'unknown_retirement_phase')
        a.require(not validate(a, record['before']), 'invalid_original_inventory')
        saved_backup = record['backup']
        a.verify_backup(Path(saved_backup['path']), saved_backup['sha256'], ids['postgres'])
        a.require(retired or record['before'] == before, 'resume_inventory_changed')
        before = record['before']
    else:
        a.require(not retired, 'original_retirement_report_required')
        saved_backup = backup(a, ids['postgres'])
        record = {'phase': 'backed_up', 'backup': saved_backup, 'before': before, 'created_at': datetime.now(timezone.utc).isoformat()}
        a.save_private(REPORT, record)
    if not retired:
        retire(a, ids['postgres'], before, commit=False)
        a.require(inventory(a, ids['postgres']) == before, 'rollback_rehearsal_changed_inventory')
        record['rollback_rehearsal_passed'] = True
        a.save_private(REPORT, record, True)
        retire(a, ids['postgres'], before)
        record['phase'] = 'retired'
        a.save_private(REPORT, record, True)
    after = inventory(a, ids['postgres'])
    a.require(validate(a, after), 'retirement_incomplete')
    a.require(before['protected'] == after['protected'], 'protected_resources_changed')
    a.require(before['archived_assets'] == after['archived_assets'], 'archived_assets_changed')
    record.update({'phase': 'verified', 'after': after, 'api_checks': verify_api(a, address)})
    a.save_private(REPORT, record, True)
    print(json.dumps({'phase': record['phase'], 'backup': saved_backup, 'report': str(REPORT), 'protected_resources_unchanged': True, 'api_checks': record['api_checks']}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'error': type(error).__name__, 'code': str(error) if type(error).__name__ == 'HandoverError' else 'retirement_failed'}))
        raise SystemExit(1)
