import contextlib
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('normalize_delivery_names', Path(__file__).with_name('normalize-delivery-names.py'))
normalize = importlib.util.module_from_spec(spec)
spec.loader.exec_module(normalize)


def snapshot(state='old'):
    return {key: {'id': target['id'], 'name': target[state], 'guard_ok': True} for key, target in normalize.TARGETS.items()}


def metadata():
    return {'id': 'a' * 64, 'name': '/' + normalize.CONTAINER, 'running': True, 'labels': {'com.docker.compose.project': normalize.PROJECT, 'com.docker.compose.service': 'postgres'}}


class NormalizeDeliveryNamesTests(unittest.TestCase):
    def test_remote_context_is_rejected_before_container_or_database_access(self):
        for endpoint in ['ssh://server', 'tcp://127.0.0.1:2375', 'tcp://server:2376', None]:
            with self.subTest(endpoint=endpoint), patch.object(normalize, 'checked_run', side_effect=['desktop-linux', json.dumps(endpoint)]) as run:
                with self.assertRaisesRegex(normalize.NormalizeError, 'nonlocal_docker_context_refused'):
                    normalize.docker_target()
                self.assertEqual(run.call_count, 2)

    def test_context_and_container_id_are_pinned_for_exec(self):
        info = metadata()
        with patch.object(normalize, 'checked_run', side_effect=['desktop-linux', json.dumps('npipe:////./pipe/dockerDesktopLinuxEngine'), json.dumps(info)]) as run:
            prefix, target = normalize.docker_target()
            self.assertEqual(prefix, ['docker', '--context', 'desktop-linux'])
            self.assertEqual(target, info['id'])
            self.assertIn(normalize.CONTAINER, run.call_args.args[0])
            self.assertNotIn('.Config.Env', ' '.join(run.call_args.args[0]))

    def test_wrong_project_service_name_and_stopped_container_are_rejected(self):
        variants = [metadata() for _ in range(4)]
        variants[0]['labels']['com.docker.compose.project'] = 'production'
        variants[1]['labels']['com.docker.compose.service'] = 'app'
        variants[2]['name'] = '/another-postgres'
        variants[3]['running'] = False
        for info in variants:
            with self.subTest(info=info), patch.object(normalize, 'checked_run', side_effect=['local', json.dumps('unix:///var/run/docker.sock'), json.dumps(info)]):
                with self.assertRaises(normalize.NormalizeError):
                    normalize.docker_target()

    def test_default_transaction_and_command_are_read_only(self):
        with patch.object(normalize, 'checked_run', return_value=json.dumps(snapshot())) as run:
            normalize.execute(['docker', '--context', 'local'], 'a' * 64)
            command = run.call_args.args[0]
            sql = run.call_args.kwargs['input']
            self.assertIn('PGOPTIONS=-c default_transaction_read_only=on', command)
            self.assertIn('READ ONLY;', sql)
            self.assertTrue(sql.endswith('ROLLBACK;'))
            self.assertNotRegex(sql.upper(), r'\b(UPDATE|DELETE|INSERT|ALTER|TRUNCATE|DROP)\b')

    def test_apply_locks_and_validates_all_four_fixed_targets_before_updates(self):
        sql = normalize.transaction_sql(apply=True)
        self.assertIn('ISOLATION LEVEL SERIALIZABLE', sql)
        self.assertEqual(sql.count('FOR UPDATE;'), 4)
        self.assertEqual(sql.count('RAISE EXCEPTION'), 4)
        self.assertLess(sql.rfind('RAISE EXCEPTION'), sql.index('UPDATE tenants SET'))
        updates = re.findall(r'UPDATE (\w+) SET (.+?) WHERE (.+?);', sql)
        self.assertEqual(len(updates), 4)
        self.assertEqual({table for table, _, _ in updates}, {'tenants', 'users', 'knowledge_bases', 'data_sources'})
        for table, assignments, where in updates:
            self.assertRegex(assignments, r"^(name|username) = '[^']*', updated_at = CURRENT_TIMESTAMP$")
            self.assertIn('id = ', where)
        self.assertIn('managed-feishu-sync:' + normalize.SPACE_ID, sql)
        self.assertIn("'allowed_space_id' = '" + normalize.SPACE_ID + "'", sql)
        self.assertIn("'resource_ids' = '[\"" + normalize.SPACE_ID + "\"]'::jsonb", sql)
        self.assertTrue(sql.endswith('COMMIT;'))
        self.assertNotRegex(sql.lower(), r'password|email|token|credentials|select \*')

    def test_restore_uses_same_guards_and_changes_only_exact_new_names(self):
        sql = normalize.transaction_sql(apply=True, restore=True)
        for key, target in normalize.TARGETS.items():
            self.assertIn(normalize.GUARDS[key], sql)
            self.assertIn('SET ' + target['field'] + ' = ' + normalize.sql_literal(target['old']), sql)
            self.assertIn('AND ' + target['field'] + ' = ' + normalize.sql_literal(target['new']), sql)
        self.assertIn("local-smoke-admin''s Workspace", sql)

    def test_unknown_names_scope_failures_missing_or_extra_fields_fail_closed(self):
        for key in normalize.TARGETS:
            for change in [{'name': 'unexpected'}, {'guard_ok': False}, {'guard_ok': None}, {'id': 'other'}, {'credentials': 'private'}]:
                current = snapshot()
                current[key].update(change)
                with self.subTest(key=key, change=change), self.assertRaises(normalize.NormalizeError):
                    normalize.validate_snapshot(current)
            current = snapshot()
            current[key] = None
            with self.assertRaises(normalize.NormalizeError):
                normalize.validate_snapshot(current)

    def test_already_applied_and_mixed_known_names_are_idempotent(self):
        current = snapshot('new')
        self.assertEqual(normalize.validate_snapshot(current), current)
        current['administrator']['name'] = normalize.TARGETS['administrator']['old']
        self.assertEqual(normalize.validate_snapshot(current), current)
        with patch.object(normalize, 'checked_run', return_value=json.dumps(snapshot('new')) + '\n' + json.dumps(snapshot('new'))):
            rows = normalize.execute(['docker', '--context', 'local'], 'a' * 64, apply=True)
            self.assertEqual(rows[0], rows[1])

    def test_main_defaults_to_plan_and_restore_does_not_imply_apply(self):
        for args in [[], ['--restore']]:
            with self.subTest(args=args), patch.object(normalize, 'docker_target', return_value=(['docker'], 'a' * 64)), patch.object(normalize, 'execute', return_value=[snapshot()]) as execute, contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(normalize.main(args), 0)
                self.assertFalse(execute.call_args.args[2])
                report = json.loads(output.getvalue())
                self.assertEqual(report['status'], 'PLAN')
                self.assertEqual(len(report['changes']), 4)

    def test_subprocess_stderr_and_stdout_are_never_exposed_on_failure(self):
        result = subprocess.CompletedProcess([], 1, 'PRIVATE_TOKEN', 'PRIVATE_PASSWORD')
        with patch.object(normalize.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(normalize.NormalizeError, '^local_command_failed$'):
                normalize.checked_run(['docker'])


if __name__ == '__main__':
    unittest.main()
