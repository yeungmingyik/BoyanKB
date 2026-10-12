import argparse
import copy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import uuid


ROOT = Path('/opt/boyankb')
SOURCE = 'c32b43f4-41b3-4060-9820-9371bdafed8b'
KB = 'd9285ed1-0760-474b-ba82-a13347598176'
SPACE = '7610776951925509072'
SCHEDULE = '0 */30 * * * *'
API = '/api/v1/datasource/' + SOURCE
REPORT = ROOT / 'feishu-schedule-report.json'
FIELDS = ('name', 'type', 'config', 'sync_schedule', 'sync_mode', 'status', 'conflict_strategy', 'sync_deletions', 'sync_log_retention_days')


class ScheduleError(Exception):
    pass


def require(value, code):
    if not value:
        raise ScheduleError(code)


def helper():
    spec = importlib.util.spec_from_file_location('schedule_accounts', ROOT / 'scripts/tencent-accounts.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.host_guard()
    return module


def validate(data):
    require(isinstance(data, dict), 'INVALID_SOURCE_RESPONSE')
    expected = {'id': SOURCE, 'tenant_id': 10000, 'knowledge_base_id': KB, 'type': 'feishu', 'sync_mode': 'incremental', 'conflict_strategy': 'overwrite', 'sync_deletions': False}
    require(all(data.get(key) == value for key, value in expected.items()), 'SOURCE_SCOPE_OR_POLICY_CHANGED')
    require(data.get('status') in ('active', 'paused', 'error'), 'UNEXPECTED_SOURCE_STATUS')
    require(data.get('sync_schedule') in ('', SCHEDULE), 'UNEXPECTED_SCHEDULE')
    require(isinstance(data.get('name'), str) and 0 < len(data['name']) <= 200, 'INVALID_SOURCE_NAME')
    require(type(data.get('sync_log_retention_days')) is int and 1 <= data['sync_log_retention_days'] <= 3650, 'INVALID_RETENTION')
    config = data.get('config')
    require(isinstance(config, dict) and set(config) <= {'type', 'resource_ids', 'settings'}, 'UNEXPECTED_CONFIG_FIELDS')
    require(config.get('type') in ('', 'feishu') and config.get('resource_ids') == [SPACE], 'EXACT_WIKI_SPACE_REQUIRED')
    settings = config.get('settings')
    require(isinstance(settings, dict) and set(settings) <= {'allowed_space_id', 'timezone', 'base_url'}, 'UNEXPECTED_SOURCE_SETTINGS')
    require(settings.get('allowed_space_id') == SPACE, 'WIKI_SCOPE_GUARD_REQUIRED')
    require(settings.get('base_url') in (None, '', 'https://open.feishu.cn'), 'OFFICIAL_FEISHU_ORIGIN_REQUIRED')
    require(settings.get('timezone') in (None, 'Asia/Shanghai'), 'UNEXPECTED_SOURCE_TIMEZONE')
    require(data.get('credentials', {}).get('credentials', {}).get('configured') is True, 'CONFIGURED_CREDENTIALS_REQUIRED')
    return {key: copy.deepcopy(data[key]) for key in FIELDS}


def desired(data, mode):
    result = validate(data)
    require(mode in ('enable', 'pause'), 'INVALID_MODE')
    result['status'] = 'active' if mode == 'enable' else 'paused'
    if mode == 'enable':
        result['sync_schedule'] = SCHEDULE
    return result


def database_state(a, postgres):
    return a.database(postgres, "SELECT jsonb_build_object('source_count',(SELECT count(*) FROM data_sources WHERE id='" + SOURCE + "' AND tenant_id=10000 AND knowledge_base_id='" + KB + "' AND deleted_at IS NULL),'config_digest',(SELECT md5(config::jsonb::text) FROM data_sources WHERE id='" + SOURCE + "' AND deleted_at IS NULL),'running_syncs',(SELECT count(*) FROM sync_logs WHERE data_source_id='" + SOURCE + "' AND status='running'));")


def summary(data):
    return {key: data[key] for key in ('status', 'sync_schedule', 'sync_mode', 'conflict_strategy', 'sync_deletions')}


def update_payload(target):
    return {key: copy.deepcopy(target[key]) for key in FIELDS if key != 'config'}


def apply(a, client, postgres, before, target, state, backup_path):
    require(state.get('source_count') == 1 and state.get('running_syncs') == 0, 'SOURCE_SYNC_RUNNING_OR_MISSING')
    require(validate(client.request('GET', API)) == validate(before), 'SOURCE_CHANGED_BEFORE_APPLY')
    require(database_state(a, postgres) == state, 'DATABASE_CHANGED_BEFORE_APPLY')
    a.save_private(backup_path, {'source_id': SOURCE, 'knowledge_base_id': KB, 'before': validate(before), 'requested': target})
    try:
        client.request('PUT', API, update_payload(target))
        after = client.request('GET', API)
        require(validate(after) == target, 'SCHEDULE_NOT_PERSISTED')
        stored = database_state(a, postgres)
        require(stored.get('source_count') == 1 and stored.get('config_digest') == state['config_digest'], 'SOURCE_CONFIGURATION_CHANGED')
    except Exception:
        try:
            client.request('POST', API + '/pause')
            require(client.request('GET', API).get('status') == 'paused', 'PAUSE_NOT_VERIFIED')
        except Exception:
            raise ScheduleError('APPLY_FAILED_PAUSE_UNVERIFIED') from None
        raise ScheduleError('APPLY_FAILED_SOURCE_PAUSED') from None
    return after


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('enable', 'pause'))
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    a = helper()
    ids, address = a.containers()
    values = a.credentials(ROOT / '.env')
    client = a.Client(address)
    client.token = client.login(a.NEW_EMAILS[0], values['ADMIN_PASSWORD'])
    user = client.request('GET', '/api/v1/auth/me')['data']
    require(user['user']['email'] == a.NEW_EMAILS[0] and user['user']['is_system_admin'], 'ADMIN_IDENTITY_REQUIRED')
    require(any(row['tenant_id'] == 10000 and row['role'] in ('owner', 'admin') for row in user['memberships']), 'HEADQUARTERS_ADMIN_REQUIRED')
    before = client.request('GET', API)
    original = validate(before)
    target = desired(before, args.mode)
    state = database_state(a, ids['postgres'])
    require(state.get('source_count') == 1, 'EXACT_SOURCE_REQUIRED')
    report = {'mode': 'apply' if args.apply else 'plan', 'source_id': SOURCE, 'knowledge_base_id': KB, 'wiki_space_id': SPACE, 'before': summary(before), 'requested': summary(target), 'running_syncs': state['running_syncs'], 'manual_sync_triggered': False, 'next_automatic_run_verified': False}
    if not args.apply:
        print(json.dumps(report, ensure_ascii=False))
        return
    if original == target:
        report.update({'changed': False, 'after': summary(before), 'configuration_preserved': True})
    else:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup_path = ROOT / 'backups' / ('feishu-schedule-' + stamp + '-' + uuid.uuid4().hex[:8] + '.json')
        after = apply(a, client, ids['postgres'], before, target, state, backup_path)
        report.update({'changed': True, 'after': summary(after), 'configuration_preserved': True, 'backup': str(backup_path)})
    a.save_private(REPORT, report, replace=True)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'FAILED', 'code': str(error) if isinstance(error, ScheduleError) else type(error).__name__}))
        raise SystemExit(1)
