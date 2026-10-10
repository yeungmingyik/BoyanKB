import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid


ROOT = Path('/opt/boyankb')
PROJECT = 'boyankb-tencent'
PUBLIC_IP = '193.112.17.218'
HQ = 10000
PARTNER = 10005
KB = 'd9285ed1-0760-474b-ba82-a13347598176'
OLD_EMAILS = ('aldream.lau@gmail.com', 'testpartner001@gmail.com')
NEW_EMAILS = ('liuweijian@boyantech.xyz', 'fengweijian@boyantech.xyz')
NEW_NAMES = ('刘伟健', '冯伟杰')
POLICIES = {'auth.registration_mode': 'invite_only', 'auth.default_tenant_mode': 'tenantless', 'auth.complex_password_enabled': True, 'tenant.self_service_creation_enabled': False}


class HandoverError(Exception):
    pass


def require(condition, code):
    if not condition:
        raise HandoverError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def run(args, input_data=None, timeout=120, stdin=None):
    if args and args[0] == 'docker':
        args = ['docker', '--host', 'unix:///var/run/docker.sock', *args[1:]]
    try:
        result = subprocess.run(args, input=input_data, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise HandoverError('local_command_failed') from None
    require(result.returncode == 0, 'local_command_failed')
    return result.stdout.decode('utf-8')


def private_path(path, must_exist=True):
    path = Path(path)
    require(path.is_absolute() and not path.is_symlink(), 'unsafe_file_path')
    require(path.resolve().is_relative_to(ROOT) and path.resolve() == path, 'file_outside_deployment')
    if must_exist:
        mode = path.stat()
        require(stat.S_ISREG(mode.st_mode) and mode.st_uid == 0 and not mode.st_mode & 0o077, 'private_root_file_required')
    else:
        require(path.parent.exists() and not path.exists(), 'new_output_path_required')
    return path


def load_private(path):
    return json.loads(private_path(path).read_text(encoding='utf-8'))


def save_private(path, value, replace=False):
    path = Path(path)
    if path.exists():
        private_path(path)
        require(replace, 'output_already_exists')
    else:
        private_path(path, False)
    temporary = path.with_name(path.name + '.next-' + uuid.uuid4().hex)
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
        stream.write(canonical(value) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def host_guard():
    require(sys.platform == 'linux' and os.geteuid() == 0, 'ubuntu_root_required')
    require(ROOT.is_dir() and ROOT.resolve() == ROOT, 'deployment_directory_required')
    release = {}
    for line in Path('/etc/os-release').read_text(encoding='utf-8').splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            release[key] = value.strip('"')
    require(release.get('ID') == 'ubuntu' and release.get('VERSION_ID') == '24.04', 'ubuntu_24_04_required')
    require(not any(os.environ.get(key) for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH')), 'docker_environment_override_forbidden')
    marker = load_private(ROOT / '.deployment-target.json')
    require(marker == {'schema_version': 1, 'project': PROJECT, 'public_ip': PUBLIC_IP}, 'deployment_marker_mismatch')
    context = json.loads(run(['docker', 'context', 'inspect']))
    require(len(context) == 1 and context[0].get('Endpoints', {}).get('docker', {}).get('Host') == 'unix:///var/run/docker.sock', 'local_docker_required')


def containers():
    result = {}
    address = None
    for service in ('app', 'postgres'):
        ids = run(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + PROJECT, '--filter', 'label=com.docker.compose.service=' + service]).split()
        require(len(ids) == 1, 'exact_cloud_service_required')
        item = json.loads(run(['docker', 'inspect', ids[0]]))[0]
        labels = item['Config'].get('Labels', {})
        require(labels.get('com.docker.compose.project') == PROJECT and labels.get('com.docker.compose.service') == service and item['State']['Running'], 'cloud_container_mismatch')
        require(not item['HostConfig'].get('Privileged') and item['HostConfig'].get('NetworkMode') != 'host', 'isolated_cloud_containers_required')
        result[service] = item['Id']
        if service == 'app':
            for network_id, attachment in sorted(item['NetworkSettings']['Networks'].items()):
                network = json.loads(run(['docker', 'network', 'inspect', network_id]))[0]
                if network.get('Labels', {}).get('com.docker.compose.project') != PROJECT:
                    continue
                candidate = ipaddress.ip_address(attachment['IPAddress'])
                if candidate.version == 4 and candidate.is_private and not candidate.is_loopback:
                    address = str(candidate)
                    break
    require(address is not None, 'cloud_app_private_address_required')
    return result, address


def sql_string(value):
    require('\x00' not in str(value), 'invalid_sql_value')
    return "'" + str(value).replace("'", "''") + "'"


def json_sql(value):
    return sql_string(canonical(value).decode('utf-8')) + '::jsonb'


def rows_sql(query):
    return "(SELECT coalesce(jsonb_agg(to_jsonb(q) ORDER BY to_jsonb(q)::text),'[]'::jsonb) FROM (" + query + ') q)'


def fingerprint(table, condition='TRUE', excluded=()):
    expression = 'to_jsonb(q)' + ''.join('-' + sql_string(key) for key in excluded)
    return "(SELECT jsonb_build_object('count',count(*),'md5',md5(coalesce(jsonb_agg(" + expression + " ORDER BY q.id)::text,'[]'))) FROM " + table + ' q WHERE ' + condition + ')'


def protected_sql():
    source = 'knowledge_base_id=' + sql_string(KB)
    tables = {
        'kb': fingerprint('knowledge_bases', 'id=' + sql_string(KB), ('creator_id', 'updated_at')),
        'knowledges': fingerprint('knowledges', source),
        'chunks': fingerprint('chunks', source),
        'embeddings': fingerprint('embeddings', source),
        'data_sources': fingerprint('data_sources', source),
        'agents': fingerprint('custom_agents', 'tenant_id=10000', ('created_by', 'updated_at')),
        'skills': fingerprint('tenant_skill_catalog', 'tenant_id=10000'),
        'models': fingerprint('models'),
        'storage': fingerprint('storage_backends'),
        'sessions': fingerprint('sessions'),
        'messages': fingerprint('messages'),
        'resources': fingerprint('resources'),
        'bindings': fingerprint('resource_bindings'),
        'document_jobs': fingerprint('boyan_document_jobs'),
    }
    return 'jsonb_build_object(' + ','.join(sql_string(key) + ',' + expression for key, expression in tables.items()) + ')'


def snapshot_sql():
    queries = {
        'users': 'SELECT id,username,email,tenant_id,is_active,is_system_admin,can_access_all_tenants FROM users WHERE deleted_at IS NULL',
        'members': 'SELECT id,user_id,tenant_id,role,status FROM tenant_members WHERE deleted_at IS NULL',
        'tenants': 'SELECT id,name,status FROM tenants WHERE deleted_at IS NULL',
        'kbs': 'SELECT id,tenant_id,creator_id FROM knowledge_bases WHERE deleted_at IS NULL AND NOT is_temporary',
        'agents': 'SELECT id,tenant_id,created_by,is_builtin FROM custom_agents WHERE tenant_id=10000 AND deleted_at IS NULL',
        'jobs': 'SELECT partner_key,tenant_id,hq_tenant_id,owner_user_id,status,state,revision,lease_holder,lease_until FROM partner_provisioning_jobs',
        'settings': 'SELECT key,value FROM system_settings WHERE key IN (' + ','.join(map(sql_string, POLICIES)) + ')',
        'counts': "SELECT (SELECT count(*) FROM knowledges WHERE knowledge_base_id=" + sql_string(KB) + " AND deleted_at IS NULL) AS active_documents,(SELECT count(*) FROM knowledges WHERE knowledge_base_id=" + sql_string(KB) + " AND deleted_at IS NULL AND parse_status='completed') AS completed_documents,(SELECT count(*) FROM tenant_skill_catalog WHERE tenant_id=10000 AND deleted_at IS NULL) AS skills,(SELECT count(*) FROM tenant_api_keys WHERE revoked_at IS NULL) AS api_keys,(SELECT count(*) FROM boyan_document_jobs WHERE state IN ('approved','generating','reviewing')) AS active_document_jobs,(SELECT count(*) FROM knowledges WHERE deleted_at IS NULL AND parse_status IN ('pending','processing')) AS active_ingestion,(SELECT count(*) FROM sessions WHERE tenant_id=10000 AND deleted_at IS NULL AND coalesce(trim(user_id),'')='') AS ownerless_headquarters_sessions",
    }
    items = [sql_string(key) + ',' + rows_sql(query) for key, query in queries.items()]
    items.append("'protected'," + protected_sql())
    return 'jsonb_build_object(' + ','.join(items) + ')'


def database(container, sql):
    output = run(['docker', 'exec', '-i', container, 'sh', '-c', 'exec psql -X -q -A -t -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'], sql.encode('utf-8'))
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    require(lines, 'database_response_missing')
    return json.loads(lines[-1])


def snapshot(container):
    return database(container, 'SELECT ' + snapshot_sql() + ';')


def validate_source(data):
    users = {row['email']: row for row in data['users']}
    require(set(users) == set(OLD_EMAILS) and len(data['users']) == 2, 'exact_two_old_accounts_required')
    for row in users.values():
        uuid.UUID(row['id'])
        require(row['is_active'] and not row['can_access_all_tenants'], 'old_account_state_changed')
    owner = users[OLD_EMAILS[0]]['id']
    require(users[OLD_EMAILS[0]]['is_system_admin'] and not users[OLD_EMAILS[1]]['is_system_admin'], 'old_admin_state_changed')
    require({row['id'] for row in data['tenants']} == {HQ, PARTNER}, 'unexpected_active_workspace')
    require(data['kbs'] == [{'id': KB, 'tenant_id': HQ, 'creator_id': owner}], 'single_enterprise_kb_required')
    counts = data['counts'][0]
    require(counts == {'active_documents': 19, 'completed_documents': 19, 'skills': 9, 'api_keys': 0, 'active_document_jobs': 0, 'active_ingestion': 0, 'ownerless_headquarters_sessions': 0}, 'source_counts_or_pending_work_changed')
    require({row['key']: row['value'] for row in data['settings']} == POLICIES, 'invite_only_policies_required')
    require(all(row['user_id'] in {user['id'] for user in users.values()} for row in data['members']), 'unexpected_membership')
    require(any(row['tenant_id'] == HQ and row['user_id'] == owner and row['role'] == 'owner' and row['status'] == 'active' for row in data['members']), 'old_headquarters_owner_required')
    jobs = [row for row in data['jobs'] if row['tenant_id'] == HQ]
    require(len(jobs) == 1, 'one_headquarters_preset_required')
    job = jobs[0]
    require(job['owner_user_id'] == owner and job['status'] == 'ready' and job['hq_tenant_id'] == HQ, 'headquarters_preset_state_changed')
    require(all(not row['lease_holder'] and row['lease_until'] is None for row in data['jobs']), 'provisioning_lease_present')
    require(job['state'].get('knowledge_mode') == 'shared_enterprise' and not job['state'].get('organization_id') and not job['state'].get('knowledge_base_id') and not job['state'].get('contributor_user_id') and not job['state'].get('team_manager_user_id'), 'unexpected_headquarters_preset_scope')
    agents = [row for row in data['agents'] if not row['is_builtin']]
    require(len(agents) == 3 and all(row['created_by'] == owner for row in agents), 'three_owned_agents_required')
    require(set(job['state'].get('agent_ids', {})) == {'mentor', 'planner', 'reviewer'} and set(job['state']['agent_ids'].values()) == {row['id'] for row in agents}, 'preset_agent_ids_mismatch')
    return {'old_ids': [users[email]['id'] for email in OLD_EMAILS], 'agent_ids': sorted(row['id'] for row in agents), 'partner_key': job['partner_key']}


def make_plan(data, ids):
    scope = validate_source(data)
    return {'format': 'boyankb-tencent-accounts-v1', 'public_ip': PUBLIC_IP, 'project': PROJECT, 'source_containers': ids, 'source': data, 'scope': scope, 'created_at': int(time.time()), 'new_accounts': [{'email': NEW_EMAILS[0], 'name': NEW_NAMES[0], 'role': 'owner', 'system_admin': True}, {'email': NEW_EMAILS[1], 'name': NEW_NAMES[1], 'role': 'viewer', 'system_admin': False}], 'headquarters_name': '博研文化总部', 'old_personal_history_transferred': False, 'channel_history_retained': True, 'old_partner_workspace_deleted': False}


def credentials(path):
    required = {'BOOTSTRAP_ADMIN_EMAIL', 'BOOTSTRAP_ADMIN_PASSWORD', 'ADMIN_PASSWORD', 'MEMBER_PASSWORD'}
    values = {}
    for line in private_path(path).read_text(encoding='utf-8-sig').splitlines():
        if '=' not in line or line.lstrip().startswith('#'):
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        if key in required:
            require(key not in values, 'duplicate_credential_key')
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            values[key] = value
    require(set(values) == required and values['BOOTSTRAP_ADMIN_EMAIL'] == OLD_EMAILS[0], 'bootstrap_credentials_required')
    for key in ('ADMIN_PASSWORD', 'MEMBER_PASSWORD'):
        value = values[key]
        require(12 <= len(value) <= 32 and all(re.search(pattern, value) for pattern in (r'[a-z]', r'[A-Z]', r'[0-9]', r'[^a-zA-Z0-9]')) and not any(char.isspace() for char in value), 'formal_password_policy_failed')
        require('REPLACE' not in value and 'CHANGE_ME' not in value, 'password_placeholder_forbidden')
    require(values['ADMIN_PASSWORD'] != values['MEMBER_PASSWORD'], 'distinct_passwords_required')
    return values


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, address):
        candidate = ipaddress.ip_address(address)
        require(candidate.version == 4 and candidate.is_private and not candidate.is_loopback, 'private_app_address_required')
        self.base_url = 'http://' + str(candidate) + ':8080'
        self.token = ''
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, method, path, body=None, token=None, accepted=(200,)):
        require(path.startswith('/api/v1/') and not any(char in path for char in ('\r', '\n', '#')), 'invalid_api_path')
        headers = {'Content-Type': 'application/json', 'X-Tenant-ID': str(HQ)}
        bearer = self.token if token is None else token
        if bearer:
            headers['Authorization'] = 'Bearer ' + bearer
        request = urllib.request.Request(self.base_url + path, data=canonical(body) if body is not None else None, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=60) as response:
                require(response.status in accepted, 'unexpected_api_status')
                raw = response.read(8 * 1024 * 1024 + 1)
                require(len(raw) <= 8 * 1024 * 1024, 'api_response_too_large')
                return json.loads(raw)
        except urllib.error.HTTPError as error:
            if error.code in accepted:
                return {'http_status': error.code}
            raise HandoverError('api_http_' + str(error.code)) from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            raise HandoverError('api_request_failed') from None

    def login(self, email, password):
        result = self.request('POST', '/api/v1/auth/login', {'email': email, 'password': password}, token='')
        token = result.get('token')
        require(isinstance(token, str) and len(token) > 20, 'login_token_missing')
        return token


def verify_identity(client, token, identity, role, admin):
    data = client.request('GET', '/api/v1/auth/me', token=token)['data']
    user = data['user']
    require(user['id'] == identity and user['is_active'] and user['is_system_admin'] == admin and not user['can_access_all_tenants'], 'independent_identity_invalid')
    memberships = data.get('memberships', [])
    require(len(memberships) == 1 and memberships[0]['tenant_id'] == HQ and memberships[0]['role'] == role and data['tenant']['id'] == HQ, 'single_headquarters_membership_required')


def compare_resume(current, original, new_ids):
    reduced = json.loads(json.dumps(current))
    reduced['users'] = [row for row in reduced['users'] if row['id'] not in new_ids]
    reduced['members'] = [row for row in reduced['members'] if row['user_id'] not in new_ids]
    require(reduced == original, 'source_changed_since_plan')
    require(all(row['tenant_id'] == HQ and row['role'] in ('owner', 'viewer') and row['status'] == 'active' for row in current['members'] if row['user_id'] in new_ids), 'new_account_scope_changed')


def provision(client, plan, progress, progress_path, values, postgres):
    client.token = client.login(OLD_EMAILS[0], values['BOOTSTRAP_ADMIN_PASSWORD'])
    old = client.request('GET', '/api/v1/auth/me')['data']['user']
    require(old['id'] == plan['scope']['old_ids'][0] and old['is_system_admin'] and not old['can_access_all_tenants'], 'bootstrap_identity_mismatch')
    tokens = []
    ids = []
    for index, email in enumerate(NEW_EMAILS):
        name = progress['temporary_admin_name'] if index == 0 else NEW_NAMES[1]
        password = values['ADMIN_PASSWORD' if index == 0 else 'MEMBER_PASSWORD']
        row = client.request('POST', '/api/v1/system/admin/users/create', {'username': name, 'email': email, 'password': password}, accepted=(200, 201))['user']
        identity = str(uuid.UUID(row['id']))
        require(row['email'] == email and row['username'] == name and identity not in plan['scope']['old_ids'] and not row['can_access_all_tenants'], 'new_identity_mismatch')
        require(not progress['new_ids'].get(email) or progress['new_ids'][email] == identity, 'resume_identity_mismatch')
        progress['new_ids'][email] = identity
        save_private(progress_path, progress, True)
        ids.append(identity)
        if index == 0:
            client.request('POST', '/api/v1/system/admin/promote', {'user_id': identity})
        current = snapshot(postgres)
        memberships = [member for member in current['members'] if member['user_id'] == identity]
        role = 'owner' if index == 0 else 'viewer'
        if not memberships:
            client.request('POST', '/api/v1/tenants/10000/members', {'email': email, 'role': role}, accepted=(200, 201))
        else:
            require(len(memberships) == 1 and memberships[0]['tenant_id'] == HQ and memberships[0]['role'] == role and memberships[0]['status'] == 'active', 'existing_membership_mismatch')
        token = client.login(email, password)
        verify_identity(client, token, identity, role, index == 0)
        tokens.append(token)
    progress['phase'] = 'new_accounts_verified'
    save_private(progress_path, progress, True)
    compare_resume(snapshot(postgres), plan['source'], ids)
    return ids, tokens, client.token


def transaction_sql(plan, before, new_ids):
    owner, member = (str(uuid.UUID(value)) for value in new_ids)
    require(owner != member and not set(new_ids) & set(plan['scope']['old_ids']), 'new_ids_must_be_independent')
    old = ','.join(sql_string(str(uuid.UUID(value))) for value in plan['scope']['old_ids'])
    agent_ids = ','.join(sql_string(str(uuid.UUID(value))) for value in plan['scope']['agent_ids'])
    tables = 'users,auth_tokens,tenant_members,tenant_invitations,tenants,knowledge_bases,knowledges,chunks,embeddings,data_sources,custom_agents,partner_provisioning_jobs,tenant_skill_catalog,models,storage_backends,sessions,messages,resources,resource_bindings,boyan_document_jobs,tenant_api_keys,system_settings'
    items = ["BEGIN;", "SET LOCAL standard_conforming_strings=on;", "SET LOCAL lock_timeout='15s';", "SET LOCAL statement_timeout='120s';", 'LOCK TABLE ' + tables + ' IN SHARE ROW EXCLUSIVE MODE;', 'SELECT 1/(CASE WHEN ' + snapshot_sql() + '=' + json_sql(before) + ' THEN 1 ELSE 0 END);']
    for index, email in enumerate(NEW_EMAILS):
        identity = new_ids[index]
        role = 'owner' if index == 0 else 'viewer'
        items.append("SELECT 1/(CASE WHEN EXISTS(SELECT 1 FROM users u JOIN tenant_members m ON m.user_id=u.id WHERE u.id=" + sql_string(identity) + ' AND u.email=' + sql_string(email) + ' AND u.is_active AND u.is_system_admin=' + ('true' if index == 0 else 'false') + " AND NOT u.can_access_all_tenants AND u.deleted_at IS NULL AND m.tenant_id=10000 AND m.role=" + sql_string(role) + " AND m.status='active' AND m.deleted_at IS NULL) THEN 1 ELSE 0 END);")
    items += [
        "UPDATE users SET username='retired-'||id,updated_at=now() WHERE id IN (" + old + ');',
        'UPDATE users SET username=' + sql_string(NEW_NAMES[0]) + ",tenant_id=10000,preferences=preferences-'last_active_tenant_id',updated_at=now() WHERE id=" + sql_string(owner) + ';',
        "UPDATE users SET tenant_id=10000,preferences=preferences-'last_active_tenant_id',updated_at=now() WHERE id=" + sql_string(member) + ';',
        'UPDATE tenants SET name=' + sql_string('博研文化总部') + ',updated_at=now() WHERE id=10000;',
        'UPDATE knowledge_bases SET creator_id=' + sql_string(owner) + ',updated_at=now() WHERE id=' + sql_string(KB) + ' AND tenant_id=10000;',
        'UPDATE custom_agents SET created_by=' + sql_string(owner) + ',updated_at=now() WHERE tenant_id=10000 AND id IN (' + agent_ids + ');',
        'UPDATE partner_provisioning_jobs SET owner_user_id=' + sql_string(owner) + ",revision=revision+1,lease_holder='',lease_until=NULL,updated_at=now() WHERE partner_key=" + sql_string(plan['scope']['partner_key']) + ' AND tenant_id=10000;',
        'UPDATE auth_tokens SET is_revoked=true,updated_at=now() WHERE user_id IN (' + old + ');',
        "UPDATE tenant_invitations SET status='revoked',responded_at=now(),updated_at=now() WHERE status='pending' AND (invited_by IN (" + old + ') OR invitee_user_id IN (' + old + ') OR team_manager_user_id IN (' + old + '));',
        "UPDATE tenant_members SET status='suspended',deleted_at=now(),updated_at=now() WHERE user_id IN (" + old + ') AND deleted_at IS NULL;',
        'UPDATE users SET is_active=false,is_system_admin=false,can_access_all_tenants=false,deleted_at=now(),updated_at=now() WHERE id IN (' + old + ');',
        'SELECT 1/(CASE WHEN ' + protected_sql() + '=' + json_sql(plan['source']['protected']) + ' THEN 1 ELSE 0 END);',
        'SELECT 1/(CASE WHEN NOT EXISTS(SELECT 1 FROM auth_tokens WHERE user_id IN (' + old + ') AND NOT is_revoked) THEN 1 ELSE 0 END);',
        'COMMIT;',
        "SELECT jsonb_build_object('committed',true);",
    ]
    return '\n'.join(items)


def final_state(data, plan, ids):
    require(data['protected'] == plan['source']['protected'], 'preserved_data_changed')
    require(len(data['users']) == 2 and {row['email'] for row in data['users']} == set(NEW_EMAILS), 'formal_users_only_required')
    for index, email in enumerate(NEW_EMAILS):
        row = next(row for row in data['users'] if row['email'] == email)
        require(row['id'] == ids[index] and row['username'] == NEW_NAMES[index] and row['is_active'] and row['is_system_admin'] == (index == 0) and not row['can_access_all_tenants'], 'formal_identity_invalid')
    require(len(data['members']) == 2 and all(row['tenant_id'] == HQ and row['status'] == 'active' and row['role'] == ('owner' if row['user_id'] == ids[0] else 'viewer') for row in data['members']) and {row['user_id'] for row in data['members']} == set(ids), 'formal_membership_invalid')
    require(data['kbs'] == [{'id': KB, 'tenant_id': HQ, 'creator_id': ids[0]}], 'knowledge_ownership_invalid')
    require(all(row['created_by'] == ids[0] for row in data['agents'] if row['id'] in plan['scope']['agent_ids']), 'agent_ownership_invalid')
    job = next(row for row in data['jobs'] if row['tenant_id'] == HQ)
    require(job['owner_user_id'] == ids[0] and job['status'] == 'ready', 'preset_ownership_invalid')
    require(next(row for row in data['tenants'] if row['id'] == HQ)['name'] == '博研文化总部', 'headquarters_name_invalid')


def verify_backup(path, expected_hash, postgres):
    path = private_path(path)
    require(path.is_relative_to(ROOT / 'backups') and path.stat().st_size > 1024, 'cloud_backup_required')
    require(re.fullmatch('[0-9a-f]{64}', expected_hash or '') is not None, 'backup_hash_required')
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        require(stream.read(5) == b'PGDMP', 'postgres_custom_backup_required')
        stream.seek(0)
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(block)
    require(checksum.hexdigest() == expected_hash, 'backup_hash_mismatch')
    require(re.fullmatch('[0-9a-f]{64}', postgres or '') is not None, 'backup_verifier_container_invalid')
    with path.open('rb') as stream:
        run(['docker', 'exec', '-i', postgres, 'pg_restore', '--list'], stdin=stream)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--plan', type=Path, default=ROOT / 'accounts-plan.json')
    parser.add_argument('--expected-plan-hash')
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--backup-sha256')
    args = parser.parse_args(argv)
    phase = 'preflight'
    try:
        host_guard()
        ids, address = containers()
        current = snapshot(ids['postgres'])
        if not args.apply:
            plan = make_plan(current, ids)
            save_private(args.plan, plan)
            print(json.dumps({'status': 'PLAN', 'plan_sha256': digest(plan), 'old_accounts': 2, 'new_accounts': 2, 'preserved_documents': 19, 'runtime_changed': False}))
            return 0
        plan = load_private(args.plan)
        require(digest(plan) == args.expected_plan_hash and plan['format'] == 'boyankb-tencent-accounts-v1' and plan['project'] == PROJECT and plan['public_ip'] == PUBLIC_IP, 'reviewed_plan_required')
        require(plan['source_containers'] == ids and 0 <= time.time() - plan['created_at'] <= 86400, 'plan_source_changed_or_expired')
        require(plan['scope'] == validate_source(plan['source']), 'invalid_plan_scope')
        require(args.backup is not None, 'pre_handover_backup_required')
        verify_backup(args.backup, args.backup_sha256, ids['postgres'])
        values = credentials(args.env_file)
        progress_path = args.plan.with_name(args.plan.stem + '.progress.json')
        if progress_path.exists():
            progress = load_private(progress_path)
            require(progress['plan_sha256'] == args.expected_plan_hash and progress['backup_sha256'] == args.backup_sha256, 'progress_binding_mismatch')
        else:
            require(current == plan['source'], 'source_changed_before_provision')
            progress = {'plan_sha256': args.expected_plan_hash, 'backup_sha256': args.backup_sha256, 'phase': 'provisioning', 'temporary_admin_name': 'cloud-admin-' + args.expected_plan_hash[:16], 'new_ids': {}}
            save_private(progress_path, progress)
        client = Client(address)
        formal_ids = [progress['new_ids'].get(email) for email in NEW_EMAILS]
        committed = not any(row['email'] in OLD_EMAILS for row in current['users'])
        if not committed:
            recovered_ids = []
            for index, email in enumerate(NEW_EMAILS):
                rows = [row for row in current['users'] if row['email'] == email]
                if rows:
                    row = rows[0]
                    require(row['username'] == (progress['temporary_admin_name'] if index == 0 else NEW_NAMES[1]) and row['id'] not in plan['scope']['old_ids'], 'unexpected_preexisting_formal_account')
                    require(not progress['new_ids'].get(email) or progress['new_ids'][email] == row['id'], 'resume_identity_mismatch')
                    recovered_ids.append(row['id'])
            compare_resume(current, plan['source'], recovered_ids)
            phase = 'provisioning'
            formal_ids, tokens, old_token = provision(client, plan, progress, progress_path, values, ids['postgres'])
            require(containers()[0] == ids, 'containers_changed_before_transaction')
            before = snapshot(ids['postgres'])
            compare_resume(before, plan['source'], formal_ids)
            phase = 'transaction_outcome_unknown'
            require(database(ids['postgres'], transaction_sql(plan, before, formal_ids)) == {'committed': True}, 'transaction_outcome_unknown')
            progress['phase'] = 'committed'
            save_private(progress_path, progress, True)
            client.request('GET', '/api/v1/auth/me', token=old_token, accepted=(401,))
        phase = 'post_verification'
        require(all(formal_ids), 'formal_identity_checkpoint_missing')
        final_state(snapshot(ids['postgres']), plan, formal_ids)
        for index, email in enumerate(NEW_EMAILS):
            token = client.login(email, values['ADMIN_PASSWORD' if index == 0 else 'MEMBER_PASSWORD'])
            verify_identity(client, token, formal_ids[index], 'owner' if index == 0 else 'viewer', index == 0)
            web_sessions = client.request('GET', '/api/v1/sessions?source=web&page=1&page_size=20', token=token)
            require(web_sessions.get('total') == 0 and web_sessions.get('data') in (None, []), 'new_web_session_list_not_empty')
            client.request('GET', '/api/v1/knowledge-bases/' + KB, token=token)
            catalog = client.request('GET', '/api/v1/business-workflow/skills', token=token)['data']
            require(catalog.get('available') is True and len(catalog.get('skills', [])) == 9 and all(item.get('available') and item.get('hash_verified') for item in catalog['skills']), 'business_skills_not_available')
            for agent_id in plan['scope']['agent_ids']:
                client.request('GET', '/api/v1/agents/' + agent_id, token=token)
        empty_history = database(ids['postgres'], "SELECT jsonb_build_object('empty',NOT EXISTS(SELECT 1 FROM sessions WHERE user_id IN (" + ','.join(map(sql_string, formal_ids)) + ')));')
        require(empty_history == {'empty': True}, 'new_accounts_have_personal_history')
        revoked = database(ids['postgres'], 'SELECT jsonb_build_object(\'revoked\',NOT EXISTS(SELECT 1 FROM auth_tokens WHERE user_id IN (' + ','.join(map(sql_string, plan['scope']['old_ids'])) + ') AND NOT is_revoked));')
        require(revoked == {'revoked': True}, 'old_tokens_not_revoked')
        progress['phase'] = 'verified'
        save_private(progress_path, progress, True)
        print(json.dumps({'status': 'VERIFIED', 'new_user_ids': formal_ids, 'tenant_id': HQ, 'old_tokens_revoked': True, 'source_preserved': True, 'personal_history_transferred': False, 'channel_history_retained': True, 'new_web_sessions_empty': True, 'graph_modified': False}))
        return 0
    except (HandoverError, OSError, ValueError, KeyError, TypeError, StopIteration) as error:
        code = str(error) if isinstance(error, HandoverError) else 'invalid_local_state_or_response'
        print(json.dumps({'status': 'FAILED', 'phase': phase, 'code': code}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
