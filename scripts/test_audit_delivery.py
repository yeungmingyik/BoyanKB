import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('audit_delivery', Path(__file__).with_name('audit-delivery.py'))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class AuditDeliveryTests(unittest.TestCase):
    def test_target_names_reject_shell_and_option_injection(self):
        for project, container in [('x;DELETE', 'db'), ('local', '--privileged'), ('local', 'db;exit'), ('$(env)', 'db')]:
            with self.subTest(project=project, container=container), self.assertRaises(audit.AuditError):
                audit.validate_target(project, container)

    def test_mismatched_project_never_executes_sql(self):
        labels = {'com.docker.compose.project': 'other', 'com.docker.compose.service': 'postgres'}
        with patch.object(audit, 'checked_run', return_value=json.dumps(labels)) as run:
            with self.assertRaisesRegex(audit.AuditError, 'container_project_or_service_mismatch'):
                audit.read_database('weknora-local', 'WeKnora-postgres')
            self.assertEqual(run.call_count, 1)

    def test_sql_is_fixed_and_read_only(self):
        labels = {'com.docker.compose.project': 'weknora-local', 'com.docker.compose.service': 'postgres'}
        rows = {name: [] for name in audit.QUERIES}
        with patch.object(audit, 'checked_run', side_effect=[json.dumps(labels), json.dumps(rows)]) as run:
            self.assertEqual(audit.read_database('weknora-local', 'WeKnora-postgres'), rows)
            command = run.call_args.args[0]
            sql = run.call_args.kwargs['input']
            self.assertIn('default_transaction_read_only=on', ' '.join(command))
            self.assertTrue(sql.startswith('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;'))
            self.assertTrue(sql.endswith('ROLLBACK;'))
            self.assertNotIn('weknora-local', sql)
            self.assertNotRegex(sql.upper(), r'\b(DELETE|UPDATE|INSERT|DROP|TRUNCATE|ALTER)\b')

    def test_unmarked_content_and_new_hq_agents_stay_outside_candidates(self):
        data = {name: [] for name in audit.QUERIES}
        data['agents'] = [{'id': 'formal', 'name': '业务导师', 'tenant_id': 10000, 'is_builtin': False, 'deleted_at': None}]
        data['sessions'] = [{'id': 'manual', 'tenant_id': 10000, 'user_id': 'owner', 'acceptance_marker': False, 'message_count': 2, 'deleted_at': None}]
        result = audit.findings(data)
        self.assertEqual(result['conversation_content_review_required'], ['manual'])
        self.assertEqual(result['test_agent_ids'], [])
        self.assertEqual(result['protected_nonfixture_agent_ids'], ['formal'])
        self.assertEqual(result['marked_acceptance_session_ids'], [])

    def test_remaining_feishu_probe_keeps_review_exit_state(self):
        data = {name: [] for name in audit.QUERIES}
        data['knowledges'] = [{'id': audit.PROBE_ID, 'tenant_id': 10000, 'knowledge_base_id': 'hq', 'external_id': 'JJ0zw4wfjiSGwSkypq3csTVJnCc', 'deleted_at': None}]
        result = audit.findings(data)
        self.assertEqual(result['status'], 'REQUIRES_EXPLICIT_APPROVAL')
        self.assertEqual(result['source_probe_hold_ids'], [audit.PROBE_ID])
        self.assertEqual(result['test_knowledge_ids'], [])


if __name__ == '__main__':
    unittest.main()
