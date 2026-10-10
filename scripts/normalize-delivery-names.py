import argparse
import datetime
import json
import re
import subprocess
import sys


PROJECT = 'weknora-local'
CONTAINER = 'WeKnora-postgres'
SPACE_ID = '7610776951925509072'
TARGETS = {
    'tenant': {'table': 'tenants', 'id': 10000, 'field': 'name', 'old': "local-smoke-admin's Workspace", 'new': '博研总部'},
    'administrator': {'table': 'users', 'id': 'a39dc32b-e6a2-4579-b80d-ada8ef2d5769', 'field': 'username', 'old': 'local-smoke-admin', 'new': '总部管理员'},
    'knowledge_base': {'table': 'knowledge_bases', 'id': 'd9285ed1-0760-474b-ba82-a13347598176', 'field': 'name', 'old': 'feishu-' + SPACE_ID, 'new': '合作伙伴专用空间'},
    'data_source': {'table': 'data_sources', 'id': 'c32b43f4-41b3-4060-9820-9371bdafed8b', 'field': 'name', 'old': 'feishu-' + SPACE_ID, 'new': '合作伙伴专用空间'},
}
GUARDS = {
    'tenant': "deleted_at IS NULL AND status = 'active'",
    'administrator': 'deleted_at IS NULL AND tenant_id = 10000 AND is_active = TRUE AND is_system_admin = TRUE',
    'knowledge_base': "deleted_at IS NULL AND tenant_id = 10000 AND type = 'document' AND description = 'managed-feishu-sync:" + SPACE_ID + "'",
    'data_source': "deleted_at IS NULL AND tenant_id = 10000 AND knowledge_base_id = '" + TARGETS['knowledge_base']['id'] + "' AND type = 'feishu' AND status = 'paused' AND sync_mode = 'incremental' AND coalesce(sync_schedule, '') = '' AND sync_deletions = FALSE AND conflict_strategy = 'overwrite' AND config::jsonb->>'type' = 'feishu' AND config::jsonb->'resource_ids' = '[\"" + SPACE_ID + "\"]'::jsonb AND config::jsonb->'settings'->>'allowed_space_id' = '" + SPACE_ID + "' AND coalesce(config::jsonb->'settings'->>'base_url', '') IN ('', 'https://open.feishu.cn')",
}


class NormalizeError(Exception):
    pass


def checked_run(command, **kwargs):
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=60, **kwargs)
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        raise NormalizeError('local_command_failed') from None
    if result.returncode:
        raise NormalizeError('local_command_failed')
    return result.stdout


def sql_literal(value):
    if isinstance(value, int):
        return str(value)
    return "'" + value.replace("'", "''") + "'"


def docker_target():
    context = checked_run(['docker', 'context', 'show']).strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', context):
        raise NormalizeError('invalid_docker_context')
    try:
        endpoint = json.loads(checked_run(['docker', 'context', 'inspect', context, '--format', '{{json .Endpoints.docker.Host}}']))
    except (ValueError, TypeError):
        raise NormalizeError('invalid_docker_context') from None
    if endpoint not in {'unix:///var/run/docker.sock', 'unix:///run/docker.sock', 'npipe:////./pipe/docker_engine', 'npipe:////./pipe/dockerDesktopLinuxEngine'}:
        raise NormalizeError('nonlocal_docker_context_refused')
    prefix = ['docker', '--context', context]
    template = '{"name":{{json .Name}},"id":{{json .Id}},"labels":{{json .Config.Labels}},"running":{{json .State.Running}}}'
    try:
        info = json.loads(checked_run(prefix + ['container', 'inspect', CONTAINER, '--format', template]))
    except (ValueError, TypeError):
        raise NormalizeError('invalid_container_metadata') from None
    if not isinstance(info, dict) or not isinstance(info.get('labels'), dict):
        raise NormalizeError('invalid_container_metadata')
    if info.get('name') != '/' + CONTAINER or info.get('running') is not True or not re.fullmatch(r'[a-f0-9]{64}', str(info.get('id', ''))):
        raise NormalizeError('container_identity_mismatch')
    labels = info['labels']
    if labels.get('com.docker.compose.project') != PROJECT or labels.get('com.docker.compose.service') != 'postgres':
        raise NormalizeError('container_project_or_service_mismatch')
    return prefix, info['id']


def snapshot_sql():
    entries = []
    for key, target in TARGETS.items():
        entries.append(sql_literal(key) + ",(SELECT json_build_object('id',id,'name'," + target['field'] + ",'guard_ok',(" + GUARDS[key] + ')) FROM ' + target['table'] + ' WHERE id = ' + sql_literal(target['id']) + ')')
    return 'SELECT json_build_object(' + ','.join(entries) + ');'


def validate_snapshot(snapshot):
    if not isinstance(snapshot, dict) or set(snapshot) != set(TARGETS):
        raise NormalizeError('invalid_database_response')
    for key, target in TARGETS.items():
        row = snapshot[key]
        if not isinstance(row, dict) or set(row) != {'id', 'name', 'guard_ok'} or row['id'] != target['id'] or row['guard_ok'] is not True:
            raise NormalizeError('protected_target_mismatch:' + key)
        if row['name'] not in {target['old'], target['new']}:
            raise NormalizeError('unexpected_current_name:' + key)
    return snapshot


def transaction_sql(apply=False, restore=False):
    if not apply:
        return "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY; SET LOCAL statement_timeout='20s'; " + snapshot_sql() + ' ROLLBACK;'
    checks = []
    updates = []
    for key, target in TARGETS.items():
        table, field = target['table'], target['field']
        identity = 'id = ' + sql_literal(target['id'])
        allowed = field + ' IN (' + sql_literal(target['old']) + ',' + sql_literal(target['new']) + ')'
        checks.append('PERFORM 1 FROM ' + table + ' WHERE ' + identity + ' AND (' + GUARDS[key] + ') AND ' + allowed + " FOR UPDATE; IF NOT FOUND THEN RAISE EXCEPTION 'protected_target_mismatch:" + key + "'; END IF;")
        before, after = (target['new'], target['old']) if restore else (target['old'], target['new'])
        updates.append('UPDATE ' + table + ' SET ' + field + ' = ' + sql_literal(after) + ', updated_at = CURRENT_TIMESTAMP WHERE ' + identity + ' AND ' + field + ' = ' + sql_literal(before) + ';')
    return "BEGIN TRANSACTION ISOLATION LEVEL SERIALIZABLE; SET LOCAL lock_timeout='5s'; SET LOCAL statement_timeout='20s'; DO $normalize$ BEGIN " + ' '.join(checks) + ' END; $normalize$; ' + snapshot_sql() + ' ' + ' '.join(updates) + ' ' + snapshot_sql() + ' COMMIT;'


def execute(prefix, container_id, apply=False, restore=False):
    command = prefix + ['exec', '-i']
    if not apply:
        command += ['-e', 'PGOPTIONS=-c default_transaction_read_only=on']
    command += [container_id, 'sh', '-c', 'exec psql -X -q -A -t -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"']
    raw = checked_run(command, input=transaction_sql(apply, restore))
    try:
        rows = [validate_snapshot(json.loads(line)) for line in raw.splitlines() if line.strip()]
    except (ValueError, TypeError):
        raise NormalizeError('invalid_database_response') from None
    if len(rows) != (2 if apply else 1):
        raise NormalizeError('invalid_database_response')
    if apply and any(rows[-1][key]['name'] != target['old' if restore else 'new'] for key, target in TARGETS.items()):
        raise NormalizeError('post_update_name_mismatch')
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args(argv)
    try:
        prefix, container_id = docker_target()
        snapshots = execute(prefix, container_id, args.apply, args.restore)
        before = snapshots[0]
        changes = []
        for key, target in TARGETS.items():
            after = target['old' if args.restore else 'new']
            changes.append({'resource': key, 'id': target['id'], 'field': target['field'], 'before': before[key]['name'], 'after': after, 'changed': before[key]['name'] != after})
        report = {'status': 'APPLIED' if args.apply else 'PLAN', 'direction': 'restore' if args.restore else 'normalize', 'project': PROJECT, 'container': CONTAINER, 'captured_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'changes': changes}
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except NormalizeError as error:
        print(json.dumps({'status': 'ERROR', 'code': str(error)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
