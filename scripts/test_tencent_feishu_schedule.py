import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location('feishu_schedule_test', Path(__file__).with_name('tencent-feishu-schedule.py'))
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class FeishuScheduleTests(unittest.TestCase):
    def setUp(self):
        self.source = {
            'id': M.SOURCE, 'tenant_id': 10000, 'knowledge_base_id': M.KB, 'name': '合作伙伴专用空间',
            'type': 'feishu', 'status': 'paused', 'sync_schedule': '', 'sync_mode': 'incremental',
            'sync_deletions': False, 'conflict_strategy': 'overwrite', 'sync_log_retention_days': 30,
            'config': {'type': 'feishu', 'resource_ids': [M.SPACE], 'settings': {'allowed_space_id': M.SPACE, 'timezone': 'Asia/Shanghai'}},
            'credentials': {'credentials': {'configured': True}}, 'last_sync_cursor': {'private_cursor': 'not-for-payload'},
        }
        self.state = {'source_count': 1, 'config_digest': 'digest', 'running_syncs': 0}

    def rejects(self, candidate, code):
        with self.assertRaises(M.ScheduleError) as raised:
            M.validate(candidate)
        self.assertEqual(str(raised.exception), code)

    def test_enable_changes_only_schedule_and_status(self):
        before = copy.deepcopy(self.source)
        payload = M.desired(self.source, 'enable')
        payload['status'] = 'paused'
        payload['sync_schedule'] = ''
        self.assertEqual(payload, M.validate(self.source))
        self.assertEqual(self.source, before)
        self.assertNotIn('credentials', payload)
        self.assertNotIn('last_sync_cursor', payload)
        self.assertNotIn('credentials', payload['config'])

    def test_pause_preserves_schedule_without_zero_value_update(self):
        self.source.update(status='active', sync_schedule=M.SCHEDULE)
        payload = M.desired(self.source, 'pause')
        self.assertEqual(payload['status'], 'paused')
        self.assertEqual(payload['sync_schedule'], M.SCHEDULE)

    def test_cross_scope_policy_and_delete_changes_rejected(self):
        for field, value in [('id', 'other'), ('tenant_id', 10005), ('knowledge_base_id', 'other'), ('type', 'lark'), ('sync_mode', 'full'), ('sync_deletions', True), ('conflict_strategy', 'skip')]:
            with self.subTest(field=field):
                candidate = copy.deepcopy(self.source)
                candidate[field] = value
                self.rejects(candidate, 'SOURCE_SCOPE_OR_POLICY_CHANGED')

    def test_empty_nested_type_preserved_for_existing_feishu_source(self):
        self.source['config']['type'] = ''
        payload = M.desired(self.source, 'enable')
        self.assertEqual(payload['config']['type'], '')
        self.assertEqual(payload['type'], 'feishu')
        self.assertNotIn('config', M.update_payload(payload))
        self.source['type'] = 'lark'
        self.rejects(self.source, 'SOURCE_SCOPE_OR_POLICY_CHANGED')

    def test_different_nested_connector_type_rejected(self):
        self.source['config']['type'] = 'lark'
        self.rejects(self.source, 'EXACT_WIKI_SPACE_REQUIRED')

    def test_secret_config_keys_and_unexpected_settings_rejected(self):
        candidate = copy.deepcopy(self.source)
        candidate['config']['credentials'] = {'app_secret': 'example'}
        self.rejects(candidate, 'UNEXPECTED_CONFIG_FIELDS')
        candidate = copy.deepcopy(self.source)
        candidate['config']['settings']['app_secret'] = 'example'
        self.rejects(candidate, 'UNEXPECTED_SOURCE_SETTINGS')

    def test_expanded_wiki_scope_and_custom_host_rejected(self):
        candidate = copy.deepcopy(self.source)
        candidate['config']['resource_ids'].append('other-space')
        self.rejects(candidate, 'EXACT_WIKI_SPACE_REQUIRED')
        candidate = copy.deepcopy(self.source)
        candidate['config']['settings']['allowed_space_id'] = 'other-space'
        self.rejects(candidate, 'WIKI_SCOPE_GUARD_REQUIRED')
        candidate = copy.deepcopy(self.source)
        candidate['config']['settings']['base_url'] = 'https://example.org'
        self.rejects(candidate, 'OFFICIAL_FEISHU_ORIGIN_REQUIRED')

    def test_unconfigured_credentials_rejected(self):
        self.source['credentials']['credentials']['configured'] = False
        self.rejects(self.source, 'CONFIGURED_CREDENTIALS_REQUIRED')

    def test_apply_uses_native_endpoint_and_preserves_config(self):
        target = M.desired(self.source, 'enable')
        after = {**self.source, **target}
        client = Mock()
        put_response = {key: value for key, value in after.items() if key != 'config'}
        put_response['credentials'] = {'credentials': {'configured': False}}
        client.request.side_effect = [self.source, put_response, after]
        accounts = Mock()
        with patch.object(M, 'database_state', side_effect=[self.state, self.state]):
            actual = M.apply(accounts, client, 'postgres', self.source, target, self.state, Path('/opt/boyankb/backups/example.json'))
        self.assertEqual(actual, after)
        accounts.save_private.assert_called_once()
        sent = client.request.call_args_list[1].args
        self.assertEqual(sent, ('PUT', M.API, {key: value for key, value in target.items() if key != 'config'}))
        self.assertNotIn('config', sent[2])
        self.assertNotIn('credentials', sent[2])
        self.assertEqual(client.request.call_args_list[2].args, ('GET', M.API))
        self.assertFalse(any(call.args[1].endswith('/sync') for call in client.request.call_args_list))

    def test_running_sync_rejected_without_api_mutation(self):
        self.state['running_syncs'] = 1
        client = Mock()
        with self.assertRaisesRegex(M.ScheduleError, 'SOURCE_SYNC_RUNNING_OR_MISSING'):
            M.apply(Mock(), client, 'postgres', self.source, M.desired(self.source, 'enable'), self.state, Path('/opt/boyankb/backups/example.json'))
        client.request.assert_not_called()

    def test_drift_rejected_before_backup_or_put(self):
        changed = copy.deepcopy(self.source)
        changed['name'] = '已更新名称'
        client = Mock()
        client.request.return_value = changed
        accounts = Mock()
        with self.assertRaisesRegex(M.ScheduleError, 'SOURCE_CHANGED_BEFORE_APPLY'):
            M.apply(accounts, client, 'postgres', self.source, M.desired(self.source, 'enable'), self.state, Path('/opt/boyankb/backups/example.json'))
        accounts.save_private.assert_not_called()
        self.assertEqual(client.request.call_count, 1)

    def test_config_drift_after_apply_pauses_source(self):
        target = M.desired(self.source, 'enable')
        after = {**self.source, **target}
        client = Mock()
        client.request.side_effect = [self.source, after, after, {'status': 'paused'}, self.source]
        with patch.object(M, 'database_state', side_effect=[self.state, {**self.state, 'config_digest': 'changed'}]):
            with self.assertRaisesRegex(M.ScheduleError, 'APPLY_FAILED_SOURCE_PAUSED'):
                M.apply(Mock(), client, 'postgres', self.source, target, self.state, Path('/opt/boyankb/backups/example.json'))
        self.assertEqual(client.request.call_args_list[3].args, ('POST', M.API + '/pause'))


if __name__ == '__main__':
    unittest.main()
