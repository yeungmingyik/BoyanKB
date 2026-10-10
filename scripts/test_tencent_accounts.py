import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import uuid


SPEC = importlib.util.spec_from_file_location('tencent_accounts', Path(__file__).with_name('tencent-accounts.py'))
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def uid(number):
    return str(uuid.UUID(int=number))


def source():
    agents = [{'id': uid(index), 'tenant_id': MOD.HQ, 'created_by': uid(1), 'is_builtin': False} for index in (10, 11, 12)]
    return {
        'users': [{'id': uid(index + 1), 'email': email, 'username': ['刘伟健', '卡卡'][index], 'tenant_id': [MOD.HQ, MOD.PARTNER][index], 'is_active': True, 'is_system_admin': index == 0, 'can_access_all_tenants': False} for index, email in enumerate(MOD.OLD_EMAILS)],
        'members': [{'id': 1, 'user_id': uid(1), 'tenant_id': MOD.HQ, 'role': 'owner', 'status': 'active'}, {'id': 2, 'user_id': uid(1), 'tenant_id': MOD.PARTNER, 'role': 'owner', 'status': 'active'}, {'id': 3, 'user_id': uid(2), 'tenant_id': MOD.PARTNER, 'role': 'viewer', 'status': 'active'}],
        'tenants': [{'id': MOD.HQ, 'name': '旧总部', 'status': 'active'}, {'id': MOD.PARTNER, 'name': '星辰教育有限公司', 'status': 'active'}],
        'kbs': [{'id': MOD.KB, 'tenant_id': MOD.HQ, 'creator_id': uid(1)}],
        'agents': agents,
        'jobs': [{'partner_key': 'headquarters', 'tenant_id': MOD.HQ, 'hq_tenant_id': MOD.HQ, 'owner_user_id': uid(1), 'status': 'ready', 'state': {'knowledge_mode': 'shared_enterprise', 'agent_ids': dict(zip(('mentor', 'planner', 'reviewer'), (row['id'] for row in agents)))}, 'revision': 20, 'lease_holder': '', 'lease_until': None}],
        'settings': [{'key': key, 'value': value} for key, value in MOD.POLICIES.items()],
        'counts': [{'active_documents': 19, 'completed_documents': 19, 'skills': 9, 'api_keys': 0, 'active_document_jobs': 0, 'active_ingestion': 0, 'ownerless_headquarters_sessions': 0}],
        'protected': {key: {'count': count, 'md5': key + '-synthetic'} for key, count in [('kb', 1), ('knowledges', 19), ('chunks', 27), ('embeddings', 27), ('sessions', 15), ('document_jobs', 5)]},
    }


def with_formal(data):
    current = copy.deepcopy(data)
    for index, email in enumerate(MOD.NEW_EMAILS):
        current['users'].append({'id': uid(101 + index), 'email': email, 'username': MOD.NEW_NAMES[index], 'tenant_id': MOD.HQ, 'is_active': True, 'is_system_admin': index == 0, 'can_access_all_tenants': False})
        current['members'].append({'id': 11 + index, 'user_id': uid(101 + index), 'tenant_id': MOD.HQ, 'role': 'owner' if index == 0 else 'viewer', 'status': 'active'})
    return current


class FakeClient:
    def __init__(self, data, fail_member_login=False):
        self.data = copy.deepcopy(data)
        self.token = ''
        self.calls = []
        self.fail_member_login = fail_member_login

    def login(self, email, password):
        if email == MOD.NEW_EMAILS[1] and self.fail_member_login:
            raise MOD.HandoverError('synthetic_login_failed')
        return next(row['id'] for row in self.data['users'] if row['email'] == email)

    def request(self, method, path, body=None, token=None, accepted=(200,)):
        self.calls.append((method, path))
        if path == '/api/v1/auth/me':
            identity = self.token if token is None else token
            user = next(row for row in self.data['users'] if row['id'] == identity)
            memberships = [row for row in self.data['members'] if row['user_id'] == identity]
            return {'data': {'user': user, 'memberships': memberships, 'tenant': {'id': MOD.HQ}}}
        if path.endswith('/users/create'):
            rows = [row for row in self.data['users'] if row['email'] == body['email']]
            if rows:
                return {'user': rows[0]}
            index = MOD.NEW_EMAILS.index(body['email'])
            row = {'id': uid(101 + index), 'email': body['email'], 'username': body['username'], 'tenant_id': 0, 'is_active': True, 'is_system_admin': False, 'can_access_all_tenants': False}
            self.data['users'].append(row)
            return {'user': row}
        if path.endswith('/promote'):
            next(row for row in self.data['users'] if row['id'] == body['user_id'])['is_system_admin'] = True
            return {}
        if path.endswith('/members') and method == 'POST':
            row = next(row for row in self.data['users'] if row['email'] == body['email'])
            row['tenant_id'] = MOD.HQ
            self.data['members'].append({'id': 10 + len(self.data['members']), 'user_id': row['id'], 'tenant_id': MOD.HQ, 'role': body['role'], 'status': 'active'})
            return {}
        raise AssertionError(path)


class TencentAccountsTests(unittest.TestCase):
    def setUp(self):
        self.data = source()
        self.ids = {'app': 'a' * 64, 'postgres': 'b' * 64}
        self.plan = MOD.make_plan(self.data, self.ids)

    def test_plan_has_exact_scope_without_credentials(self):
        self.assertEqual(self.plan['scope']['old_ids'], [uid(1), uid(2)])
        self.assertEqual(self.plan['scope']['agent_ids'], [uid(10), uid(11), uid(12)])
        self.assertEqual(self.plan['new_accounts'][1]['role'], 'viewer')
        self.assertNotIn('password_hash', json.dumps(self.plan))
        self.assertNotIn('ADMIN_PASSWORD', json.dumps(self.plan))
        self.assertFalse(self.plan['old_personal_history_transferred'])

    def test_unknown_account_or_workspace_blocks_plan(self):
        for section, row in [('users', dict(self.data['users'][1], email='unexpected@example.invalid', id=uid(4))), ('tenants', {'id': 90000, 'name': 'unknown'})]:
            data = copy.deepcopy(self.data)
            data[section].append(row)
            with self.assertRaises(MOD.HandoverError):
                MOD.make_plan(data, self.ids)

    def test_non_source_knowledge_base_blocks_plan(self):
        self.data['kbs'].append({'id': uid(80), 'tenant_id': MOD.HQ, 'creator_id': uid(1)})
        with self.assertRaisesRegex(MOD.HandoverError, 'single_enterprise'):
            MOD.make_plan(self.data, self.ids)

    def test_open_registration_blocks_plan(self):
        self.data['settings'][0]['value'] = 'open'
        with self.assertRaisesRegex(MOD.HandoverError, 'invite_only'):
            MOD.make_plan(self.data, self.ids)

    def test_work_in_flight_and_api_keys_block_plan(self):
        for key in ('api_keys', 'active_document_jobs', 'active_ingestion', 'ownerless_headquarters_sessions'):
            data = copy.deepcopy(self.data)
            data['counts'][0][key] = 1
            with self.assertRaisesRegex(MOD.HandoverError, 'pending_work'):
                MOD.make_plan(data, self.ids)

    def test_live_provisioning_lease_blocks_plan(self):
        self.data['jobs'][0]['lease_holder'] = uid(7)
        with self.assertRaisesRegex(MOD.HandoverError, 'lease'):
            MOD.make_plan(self.data, self.ids)

    def test_mismatched_agent_owner_blocks_plan(self):
        self.data['agents'][0]['created_by'] = uid(2)
        with self.assertRaisesRegex(MOD.HandoverError, 'owned_agents'):
            MOD.make_plan(self.data, self.ids)

    def test_resume_allows_only_two_formal_identity_additions(self):
        current = with_formal(self.data)
        MOD.compare_resume(current, self.data, [uid(101), uid(102)])
        current['protected']['chunks']['md5'] = 'changed'
        with self.assertRaisesRegex(MOD.HandoverError, 'source_changed'):
            MOD.compare_resume(current, self.data, [uid(101), uid(102)])

    def test_resume_forbids_additional_workspace_membership(self):
        current = with_formal(self.data)
        current['members'][-1]['tenant_id'] = MOD.PARTNER
        with self.assertRaisesRegex(MOD.HandoverError, 'scope_changed'):
            MOD.compare_resume(current, self.data, [uid(101), uid(102)])

    def test_transaction_preserves_fk_and_personal_history(self):
        sql = MOD.transaction_sql(self.plan, with_formal(self.data), [uid(101), uid(102)])
        self.assertNotIn('DELETE FROM', sql)
        for table in ('sessions', 'messages', 'boyan_document_jobs', 'resources', 'resource_bindings', 'tenant_skill_catalog', 'knowledges', 'chunks', 'embeddings'):
            self.assertNotIn('UPDATE ' + table + ' ', sql)
        self.assertIn('UPDATE auth_tokens SET is_revoked=true', sql)
        self.assertIn("status='suspended',deleted_at=now()", sql)
        self.assertIn('UPDATE custom_agents SET created_by=', sql)
        self.assertIn('UPDATE partner_provisioning_jobs SET owner_user_id=', sql)
        self.assertLess(sql.index('LOCK TABLE'), sql.index('UPDATE users'))
        self.assertLess(sql.index("username='retired-'||id"), sql.index("username='刘伟健'"))
        self.assertLess(sql.index('"sessions-synthetic"'), sql.index('COMMIT;'))
        self.assertNotIn('WHERE id=10005', sql)

    def test_transaction_refuses_recycled_uuid(self):
        with self.assertRaisesRegex(MOD.HandoverError, 'independent'):
            MOD.transaction_sql(self.plan, self.data, [uid(1), uid(102)])
        with self.assertRaisesRegex(MOD.HandoverError, 'independent'):
            MOD.transaction_sql(self.plan, self.data, [uid(101), uid(101)])

    def test_sql_values_escape_quotes_without_dollar_blocks(self):
        value = "x'; SELECT 'unsafe'; -- $guard$"
        self.assertEqual(MOD.sql_string(value), "'x''; SELECT ''unsafe''; -- $guard$'")
        self.assertNotIn('DO $', MOD.transaction_sql(self.plan, self.data, [uid(101), uid(102)]))
        with self.assertRaises(MOD.HandoverError):
            MOD.sql_string('x\x00y')

    def test_credentials_validate_without_echo(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('BOOTSTRAP_ADMIN_EMAIL=' + MOD.OLD_EMAILS[0] + '\nBOOTSTRAP_ADMIN_PASSWORD=old-secret\nADMIN_PASSWORD=FormalTestPass1!\nMEMBER_PASSWORD=MemberTestPass2!\nUNRELATED_SECRET=never-return\n', encoding='utf-8')
            with mock.patch.object(MOD, 'private_path', return_value=path):
                values = MOD.credentials(path)
            self.assertNotIn('UNRELATED_SECRET', values)
            path.write_text(path.read_text().replace('MemberTestPass2!', 'FormalTestPass1!'))
            with mock.patch.object(MOD, 'private_path', return_value=path):
                with self.assertRaisesRegex(MOD.HandoverError, '^distinct_passwords_required$'):
                    MOD.credentials(path)

    def test_non_linux_host_rejected_before_docker(self):
        with mock.patch.object(MOD.sys, 'platform', 'win32'), mock.patch.object(MOD, 'run') as runner:
            with self.assertRaisesRegex(MOD.HandoverError, 'ubuntu_root'):
                MOD.host_guard()
            runner.assert_not_called()

    def test_cloud_container_labels_must_match_exactly(self):
        item = {'Config': {'Labels': {'com.docker.compose.project': 'weknora-local', 'com.docker.compose.service': 'app'}}, 'State': {'Running': True}}
        with mock.patch.object(MOD, 'run', side_effect=['abc\n', json.dumps([item])]):
            with self.assertRaisesRegex(MOD.HandoverError, 'cloud_container_mismatch'):
                MOD.containers()

    def test_public_api_addresses_and_redirects_rejected(self):
        for address in ('193.112.17.218', '8.8.8.8', '127.0.0.1', '::1'):
            with self.assertRaises(MOD.HandoverError):
                MOD.Client(address)
        self.assertIsNone(MOD.NoRedirect().redirect_request(None, None, 302, '', None, 'https://other.invalid'))

    def test_default_plan_never_reads_credentials_or_calls_api(self):
        out = io.StringIO()
        with mock.patch.object(MOD, 'host_guard'), mock.patch.object(MOD, 'containers', return_value=(self.ids, '172.28.0.4')), mock.patch.object(MOD, 'snapshot', return_value=self.data), mock.patch.object(MOD, 'save_private'), mock.patch.object(MOD, 'credentials') as secrets, mock.patch.object(MOD, 'Client') as client, mock.patch.object(MOD.sys, 'stdout', out):
            self.assertEqual(MOD.main([]), 0)
        secrets.assert_not_called()
        client.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())['status'], 'PLAN')

    def test_apply_wrong_plan_hash_cannot_read_password_or_mutate(self):
        with mock.patch.object(MOD, 'host_guard'), mock.patch.object(MOD, 'containers', return_value=(self.ids, '172.28.0.4')), mock.patch.object(MOD, 'snapshot', return_value=self.data), mock.patch.object(MOD, 'load_private', return_value=self.plan), mock.patch.object(MOD, 'credentials') as secrets, mock.patch.object(MOD, 'Client') as client, mock.patch.object(MOD.sys, 'stderr', io.StringIO()) as output:
            self.assertEqual(MOD.main(['--apply', '--expected-plan-hash', 'wrong']), 1)
            self.assertEqual(json.loads(output.getvalue())['code'], 'reviewed_plan_required')
        secrets.assert_not_called()
        client.assert_not_called()

    def test_final_verification_requires_formal_names_and_owners(self):
        data = with_formal(self.data)
        data['users'] = data['users'][-2:]
        data['members'] = data['members'][-2:]
        data['kbs'][0]['creator_id'] = uid(101)
        for row in data['agents']:
            row['created_by'] = uid(101)
        data['jobs'][0]['owner_user_id'] = uid(101)
        data['tenants'][0]['name'] = '博研文化总部'
        MOD.final_state(data, self.plan, [uid(101), uid(102)])
        data['jobs'][0]['owner_user_id'] = uid(1)
        with self.assertRaisesRegex(MOD.HandoverError, 'preset_ownership'):
            MOD.final_state(data, self.plan, [uid(101), uid(102)])

    def test_database_failures_never_echo_command_stderr(self):
        response = mock.Mock(returncode=1, stdout=b'', stderr=b'PASSWORD=synthetic-secret')
        with mock.patch.object(MOD.subprocess, 'run', return_value=response):
            with self.assertRaisesRegex(MOD.HandoverError, '^local_command_failed$'):
                MOD.run(['docker', 'exec', 'fake'])

    def test_repeated_provision_keeps_new_uuids_and_memberships(self):
        client = FakeClient(self.data)
        progress = {'temporary_admin_name': 'cloud-admin-synthetic', 'new_ids': {}}
        values = {'BOOTSTRAP_ADMIN_PASSWORD': 'SyntheticOld1!', 'ADMIN_PASSWORD': 'SyntheticAdmin2!', 'MEMBER_PASSWORD': 'SyntheticMember3!'}
        with mock.patch.object(MOD, 'save_private'), mock.patch.object(MOD, 'snapshot', side_effect=lambda _: copy.deepcopy(client.data)):
            first = MOD.provision(client, self.plan, progress, Path('unused'), values, 'unused')
            second = MOD.provision(client, self.plan, progress, Path('unused'), values, 'unused')
        self.assertEqual(first[0], [uid(101), uid(102)])
        self.assertEqual(second[0], first[0])
        self.assertEqual(len(client.data['users']), 4)
        self.assertEqual(len(client.data['members']), 5)
        self.assertNotIn(('POST', '/api/v1/system/admin/users/reset-password'), client.calls)
        self.assertFalse(any(method == 'DELETE' for method, _ in client.calls))

    def test_failed_new_login_never_reaches_verified_checkpoint(self):
        client = FakeClient(self.data, fail_member_login=True)
        progress = {'temporary_admin_name': 'cloud-admin-synthetic', 'new_ids': {}}
        values = {'BOOTSTRAP_ADMIN_PASSWORD': 'SyntheticOld1!', 'ADMIN_PASSWORD': 'SyntheticAdmin2!', 'MEMBER_PASSWORD': 'SyntheticMember3!'}
        with mock.patch.object(MOD, 'save_private'), mock.patch.object(MOD, 'snapshot', side_effect=lambda _: copy.deepcopy(client.data)):
            with self.assertRaisesRegex(MOD.HandoverError, 'synthetic_login_failed'):
                MOD.provision(client, self.plan, progress, Path('unused'), values, 'unused')
        self.assertNotEqual(progress.get('phase'), 'new_accounts_verified')
        self.assertEqual({row['email'] for row in client.data['users'] if row['is_active']} & set(MOD.OLD_EMAILS), set(MOD.OLD_EMAILS))

    def test_local_docker_socket_is_pinned_for_each_command(self):
        with mock.patch.object(MOD.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout=b'{}')) as runner:
            MOD.run(['docker', 'inspect', 'synthetic'])
        self.assertEqual(runner.call_args.args[0][:3], ['docker', '--host', 'unix:///var/run/docker.sock'])

    def test_committed_reentry_does_not_recreate_or_retire_accounts_again(self):
        data = with_formal(self.data)
        data['users'] = data['users'][-2:]
        data['members'] = data['members'][-2:]
        data['kbs'][0]['creator_id'] = uid(101)
        for row in data['agents']:
            row['created_by'] = uid(101)
        data['jobs'][0]['owner_user_id'] = uid(101)
        data['tenants'][0]['name'] = '博研文化总部'
        plan_hash = MOD.digest(self.plan)
        progress = {'plan_sha256': plan_hash, 'backup_sha256': 'f' * 64, 'phase': 'new_accounts_verified', 'temporary_admin_name': 'cloud-admin-synthetic', 'new_ids': dict(zip(MOD.NEW_EMAILS, [uid(101), uid(102)]))}
        client = mock.Mock()
        client.login.return_value = 'synthetic-token'
        client.request.side_effect = lambda method, path, **kwargs: {'data': {'available': True, 'skills': [{'available': True, 'hash_verified': True}] * 9}} if path.endswith('/business-workflow/skills') else {'data': [], 'total': 0} if '/sessions?' in path else {}
        values = {'ADMIN_PASSWORD': 'SyntheticAdmin2!', 'MEMBER_PASSWORD': 'SyntheticMember3!'}
        def read_database(container, sql):
            self.assertNotIn('UPDATE ', sql)
            return {'empty': True} if "'empty'" in sql else {'revoked': True}
        with mock.patch.object(MOD, 'host_guard'), mock.patch.object(MOD, 'containers', return_value=(self.ids, '172.28.0.4')), mock.patch.object(MOD, 'snapshot', return_value=data), mock.patch.object(MOD, 'load_private', side_effect=[self.plan, progress]), mock.patch.object(MOD, 'credentials', return_value=values), mock.patch.object(MOD, 'verify_backup'), mock.patch.object(MOD.Path, 'exists', return_value=True), mock.patch.object(MOD, 'Client', return_value=client), mock.patch.object(MOD, 'provision') as provision, mock.patch.object(MOD, 'verify_identity'), mock.patch.object(MOD, 'database', side_effect=read_database), mock.patch.object(MOD, 'save_private'), mock.patch.object(MOD.sys, 'stdout', io.StringIO()):
            self.assertEqual(MOD.main(['--apply', '--expected-plan-hash', plan_hash, '--backup', '/opt/boyankb/backups/synthetic.age', '--backup-sha256', 'f' * 64]), 0)
        provision.assert_not_called()
        self.assertEqual([call.args[0] for call in client.login.call_args_list], list(MOD.NEW_EMAILS))
        self.assertEqual(progress['phase'], 'verified')

    def test_ownerless_headquarters_history_blocks_handover(self):
        self.data['counts'][0]['ownerless_headquarters_sessions'] = 1
        with self.assertRaisesRegex(MOD.HandoverError, 'source_counts'):
            MOD.make_plan(self.data, self.ids)

    def test_backup_requires_postgres_custom_header_before_list(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'backups').mkdir()
            path = root / 'backups/example.dump'
            path.write_bytes(b'other' + b'x' * 2048)
            wanted = MOD.hashlib.sha256(path.read_bytes()).hexdigest()
            with mock.patch.object(MOD, 'ROOT', root), mock.patch.object(MOD, 'private_path', return_value=path), mock.patch.object(MOD, 'run') as run:
                with self.assertRaisesRegex(MOD.HandoverError, 'postgres_custom_backup_required'):
                    MOD.verify_backup(path, wanted, 'a' * 64)
                run.assert_not_called()

    def test_backup_header_hash_and_pg_restore_list_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'backups').mkdir()
            path = root / 'backups/example.dump'
            path.write_bytes(b'PGDMP' + b'x' * 2048)
            wanted = MOD.hashlib.sha256(path.read_bytes()).hexdigest()
            with mock.patch.object(MOD, 'ROOT', root), mock.patch.object(MOD, 'private_path', return_value=path), mock.patch.object(MOD, 'run') as run:
                MOD.verify_backup(path, wanted, 'a' * 64)
                self.assertEqual(run.call_args.args[0], ['docker', 'exec', '-i', 'a' * 64, 'pg_restore', '--list'])
                self.assertIn('stdin', run.call_args.kwargs)


if __name__ == '__main__':
    unittest.main()
