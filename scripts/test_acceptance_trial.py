from contextlib import redirect_stderr, redirect_stdout
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid
import zipfile


SPEC = importlib.util.spec_from_file_location('acceptance_trial', Path(__file__).with_name('acceptance-trial.py'))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
USER_ID = '11111111-2222-4333-8444-555555555555'
AGENT_IDS = {role: str(uuid.UUID(int=index + 10)) for index, role in enumerate(MODULE.AGENTS)}
PASSWORD = 'Synthetic_Password!42'
TOKEN = 'synthetic-jwt-value-never-persist'
INVITE_TOKEN = 'synthetic-invitation-token-never-persist'
KNOWLEDGE_ID = '22222222-2222-4333-8444-555555555555'
JOB_ID = '33333333-2222-4333-8444-555555555555'


def word_package():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('_rels/.rels', '<Relationships/>')
        archive.writestr('word/document.xml', '<document><t>博研文化平台 BoyanKB 合作伙伴入门指南 资料来源</t></document>')
        archive.writestr('word/styles.xml', '<styles/>')
    return output.getvalue()


class FakeClient:
    def __init__(self):
        self.opener = SimpleNamespace(addheaders=[])
        self.calls = []
        self.tenant = 10100
        self.admin = False
        self.role = 'viewer'
        self.foreign_source = False
        self.bad_skill_hash = False
        self.team_available = True
        self.allow_hq = False
        self.failed_creation = False
        self.failed_revoke = False
        self.blocked = False
        self.invitation_state = None
        self.sessions = []
        self.skip_proposal_turns = 0
        self.job = None
        self.content = word_package()

    def request(self, path, method='GET', payload=None, principal=None, stream=False, binary=False, tenant=None):
        self.calls.append({'path': path, 'method': method, 'payload': copy.deepcopy(payload), 'tenant': tenant})
        if tenant == MODULE.HQ_TENANT and not self.allow_hq:
            return 403, {'success': False}
        if path == '/api/v1/auth/login':
            return 200, {'token': TOKEN, 'refresh_token': 'synthetic-refresh-token'}
        if path == '/api/v1/auth/me':
            return self.ok({'user': {'id': USER_ID, 'is_active': True, 'is_system_admin': self.admin}, 'memberships': [{'tenant_id': self.tenant, 'role': self.role}]})
        if path == '/api/v1/business-workflow/skills':
            skills = [self.skill(name) for name in sorted(MODULE.SKILLS)]
            return self.ok({'available': True, 'installed': True, 'status': 'ready', 'planner_agent_id': AGENT_IDS['planner'], 'skills': skills})
        if path.startswith('/api/v1/business-workflow/skills/'):
            return self.ok({**self.skill(path.rsplit('/', 1)[1]), 'content': 'synthetic approved guide'})
        if path == '/api/v1/agents' and method == 'GET':
            return self.ok([{'id': agent_id, 'is_builtin': False} for agent_id in AGENT_IDS.values()])
        if path.startswith('/api/v1/agents/') and method == 'GET':
            role = next(role for role, value in AGENT_IDS.items() if value == path.rsplit('/', 1)[1])
            return self.ok({'name': MODULE.AGENTS[role], 'tenant_id': self.tenant, 'config': {'kb_selection_mode': 'selected', 'knowledge_bases': [MODULE.SOURCE_KB], 'allowed_tools': sorted(MODULE.READ_TOOLS | ({'boyan_document'} if role == 'planner' else set())), 'mcp_selection_mode': 'none', 'skills_selection_mode': 'none', 'citation_enabled': True}})
        if path == '/api/v1/shared-knowledge-bases':
            return self.ok([{'knowledge_base': {'id': MODULE.SOURCE_KB}, 'permission': 'viewer'}])
        if path == '/api/v1/knowledge-bases' and method == 'GET':
            return self.ok([])
        if path == '/api/v1/knowledge-bases/' + MODULE.SOURCE_KB and method == 'GET':
            return self.ok({'id': MODULE.SOURCE_KB, 'tenant_id': MODULE.HQ_TENANT})
        if path == '/api/v1/business-workflow/team':
            return self.ok({'available': self.team_available, 'members': [{'user_id': USER_ID, 'role': 'viewer'}]})
        if path == '/api/v1/business-workflow/team/invitations':
            if method == 'GET':
                return self.ok({'items': [] if self.invitation_state is None else [{'id': 77, 'kind': 'link', 'status': self.invitation_state}], 'total': 0 if self.invitation_state is None else 1})
            if payload == {'kind': 'link'}:
                self.invitation_state = 'pending'
                return 201, {'success': True, 'data': {'id': 77, 'kind': 'link', 'status': 'invalid' if self.failed_creation else 'pending', 'accepted_count': 0, 'token': INVITE_TOKEN, 'invite_url': '/register?token=' + INVITE_TOKEN}}
            return 400, {'success': False}
        if path == '/api/v1/business-workflow/team/invitations/77' and method == 'DELETE':
            if self.failed_revoke:
                return 503, {'success': False}
            self.invitation_state = 'revoked'
            return self.ok({})
        if path in {'/api/v1/tenants/10000/members', '/api/v1/business-workflow/readiness'}:
            return 403, {'success': False}
        if path == '/api/v1/sessions' and method == 'POST':
            session_id = str(uuid.UUID(int=100 + len(self.sessions)))
            self.sessions.append({'id': session_id, **payload})
            return 201, {'success': True, 'data': {'id': session_id}}
        if path.startswith('/api/v1/sessions/') and '/documents' not in path:
            matches = [item for item in self.sessions if item['id'] == path.rsplit('/', 1)[1]]
            return self.ok(matches[0]) if matches else (404, {'success': False})
        if path.startswith('/api/v1/agent-chat/'):
            planner = payload['agent_id'] == AGENT_IDS['planner']
            events = [
                {'response_type': 'tool_call', 'data': {'tool_call_id': 'search', 'tool_name': 'search_knowledge', 'arguments': {'query': '教学准备'}}},
                {'response_type': 'tool_result', 'data': {'tool_call_id': 'search', 'tool_name': 'search_knowledge', 'success': True, 'results': [{'knowledge_id': KNOWLEDGE_ID, 'knowledge_base_id': MODULE.SOURCE_KB}]}},
            ]
            if planner:
                if self.skip_proposal_turns:
                    self.skip_proposal_turns -= 1
                else:
                    self.job = {'id': JOB_ID, 'session_id': path.rsplit('/', 1)[1], 'state': 'awaiting_confirmation', 'format': 'docx', 'outline_hash': 'a' * 64, 'outline_markdown': '合作伙伴入门指南'}
                    events.append({'response_type': 'tool_call', 'data': {'tool_call_id': 'proposal', 'tool_name': 'boyan_document', 'arguments': {'operation': 'propose'}}})
            events.extend([{'response_type': 'answer', 'content': '请核对教学资料，详情见已引用来源。'}, {'response_type': 'complete'}])
            return 200, io.BytesIO(''.join('data: ' + json.dumps(item, ensure_ascii=False) + '\n\n' for item in events).encode())
        if path == '/api/v1/knowledge/' + KNOWLEDGE_ID:
            return self.ok({'id': KNOWLEDGE_ID, 'knowledge_base_id': 'foreign-kb' if self.foreign_source else MODULE.SOURCE_KB})
        if '/documents' in path:
            if path.endswith('/documents'):
                return self.ok([copy.deepcopy(self.job)] if self.job else [])
            if path.endswith('/confirm'):
                if payload.get('outline_hash') != self.job['outline_hash']:
                    return 409, {'success': False}
                self.job['state'] = 'approved'
            elif path.endswith('/generate'):
                if self.job['state'] != 'approved':
                    return 409, {'success': False}
                self.job.update(state='blocked' if self.blocked else 'published', review={'passed': not self.blocked, 'issues': [{'severity': 'blocking', 'problem': 'unsupported'}] if self.blocked else []}, artifact_sha256=hashlib.sha256(self.content).hexdigest())
            elif path.endswith('/download'):
                if self.job['state'] != 'published':
                    return 409, b'' if binary else {'success': False}
                return 200, self.content
            return self.ok(copy.deepcopy(self.job))
        if method in {'POST', 'PUT'}:
            return 403, {'success': False}
        raise AssertionError((path, method))

    def skill(self, name):
        return {'name': name, 'version': 'boyan/v1', 'available': True, 'installed': True, 'hash_verified': True, 'status': 'ready', 'sha256': '0' * 64 if self.bad_skill_hash else hashlib.sha256(b'synthetic approved guide').hexdigest()}

    def ok(self, data):
        return 200, {'success': True, 'data': data}


class TrialAcceptanceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        for name, value in {'ENV_FILE': self.directory / '.env', 'PARTNER_RESULT': self.directory / 'partner-result.json', 'OUTPUT': self.directory / 'acceptance'}.items():
            item = patch.object(MODULE, name, value)
            item.start()
            self.addCleanup(item.stop)
        MODULE.ENV_FILE.write_text('TRIAL_PARTNER_EMAIL=partner@example.test\nTRIAL_PARTNER_PASSWORD=' + PASSWORD + '\nTRIAL_ADMIN_PASSWORD=must-not-load-admin\nTRIAL_TUNNEL_TOKEN=must-not-load-tunnel\n')
        self.config = {'status': 'ready', 'tenant_id': 10100, 'state': {'knowledge_mode': 'shared_enterprise', 'agent_ids': AGENT_IDS, 'team_manager_user_id': USER_ID}}
        MODULE.PARTNER_RESULT.write_text(json.dumps(self.config))
        self.client = FakeClient()

    def run_main(self, *args):
        output = io.StringIO()
        with patch.object(MODULE.BASE, 'Client', return_value=self.client), redirect_stdout(output):
            result = MODULE.main(list(args))
        summary = json.loads(output.getvalue().splitlines()[-1])
        report = json.loads(Path(summary['report']).read_text(encoding='utf-8')) if summary.get('report') else None
        return result, output.getvalue(), report

    def previous_report(self, followups=0):
        previous = MODULE.OUTPUT / 'previous'
        previous.mkdir(parents=True)
        session = {'id': str(uuid.UUID(int=200)), 'title': '合作伙伴入门指南', 'agent': 'planner'}
        self.client.sessions.append(session)
        report = {'tenant_id': self.client.tenant, 'base_url': 'http://127.0.0.1:8080', 'source_knowledge_base_id': MODULE.SOURCE_KB, 'mode': 'exercise', 'sessions': [session], 'document_followups': followups}
        (previous / 'report.json').write_text(json.dumps(report), encoding='utf-8')
        return previous

    def test_default_only_login_and_get_and_marks_exercise_unverified(self):
        code, output, report = self.run_main()
        self.assertEqual(code, 2)
        self.assertEqual(report['automated_result'], 'PASS')
        self.assertEqual(report['mode'], 'read_only')
        self.assertEqual([call['path'] for call in self.client.calls if call['method'] != 'GET'], ['/api/v1/auth/login'])
        self.assertEqual(self.client.sessions, [])
        self.assertTrue(any(item['name'] == 'exercise_not_run' for item in report['manual_checks']))
        self.assertNotIn(PASSWORD, output)
        self.assertEqual(self.client.opener.addheaders, [('User-Agent', 'BoyanKB-DeploymentCheck/1.0')])

    def test_credentials_only_load_partner_exact_values(self):
        MODULE.ENV_FILE.write_text('TRIAL_PARTNER_EMAIL=partner@example.test\nTRIAL_PARTNER_PASSWORD="a\\b#c $d"\nTRIAL_ADMIN_PASSWORD=excluded\n')
        self.assertEqual(MODULE.credentials(), {'TRIAL_PARTNER_EMAIL': 'partner@example.test', 'TRIAL_PARTNER_PASSWORD': 'a\\b#c $d'})
        MODULE.ENV_FILE.write_text(MODULE.ENV_FILE.read_text() + 'TRIAL_PARTNER_EMAIL=duplicate@example.test\n')
        with self.assertRaisesRegex(MODULE.BASE.AcceptanceError, 'duplicate_credential_key'):
            MODULE.credentials()

    def test_config_rejects_hq_private_kb_and_incomplete_bindings(self):
        for change in (lambda value: value.update(tenant_id=MODULE.HQ_TENANT), lambda value: value['state'].update(knowledge_base_id='extra-kb'), lambda value: value['state'].update(contributor_user_id=USER_ID), lambda value: value['state'].update(knowledge_mode=''), lambda value: value['state'].update(agent_ids={}), lambda value: value.update(status='provisioning')):
            value = copy.deepcopy(self.config)
            change(value)
            MODULE.PARTNER_RESULT.write_text(json.dumps(value))
            with self.assertRaises(MODULE.BASE.AcceptanceError):
                MODULE.partner_config()

    def test_disallowed_origin_cannot_construct_client(self):
        for origin in ('https://other.example.com', 'http://127.0.0.1:8080@other.example.com', 'https://kb.boyantech.xyz/extra'):
            with patch.object(MODULE.BASE, 'Client', side_effect=AssertionError('unexpected network')), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    MODULE.main(['--base-url', origin])

    def test_admin_or_non_viewer_never_exercises(self):
        for admin, role in ((True, 'viewer'), (False, 'owner')):
            self.client.admin, self.client.role = admin, role
            code, _, report = self.run_main('--exercise')
            self.assertEqual(code, 1)
            self.assertEqual(report['automated_result'], 'FAIL')
            self.assertEqual(self.client.sessions, [])
            self.assertIsNone(self.client.invitation_state)

    def test_catalog_tampering_stops_before_models(self):
        self.client.bad_skill_hash = True
        code, _, report = self.run_main('--exercise')
        self.assertEqual(code, 1)
        self.assertTrue(any(item.get('code') == 'skill_integrity_failed' for item in report['checks']))
        self.assertFalse(any('/agent-chat/' in call['path'] for call in self.client.calls))

    def test_forged_hq_header_must_be_denied(self):
        self.client.allow_hq = True
        code, _, report = self.run_main()
        self.assertEqual(code, 1)
        self.assertTrue(any(item['name'] == 'isolation.hq_agents_header_denied' and item['status'] == 'FAIL' for item in report['checks']))

    def test_full_exercise_confirmation_invitation_cleanup_and_office_inspection(self):
        code, output, report = self.run_main('--exercise')
        self.assertEqual(code, 2)
        self.assertEqual(report['automated_result'], 'PASS')
        self.assertEqual(self.client.invitation_state, 'revoked')
        self.assertEqual(len(self.client.sessions), 2)
        self.assertTrue(all('验收' not in item['title'] and 'test' not in item['title'] for item in self.client.sessions))
        self.assertEqual(len(report['artifacts']), 1)
        self.assertEqual(report['artifacts'][0]['sha256'], hashlib.sha256(self.client.content).hexdigest())
        confirmations = [call for call in self.client.calls if call['path'].endswith('/confirm')]
        self.assertEqual([item['payload']['outline_hash'] for item in confirmations], ['0' * 64, 'a' * 64])
        self.assertFalse(any(call['path'].startswith('/api/v1/auth/register') for call in self.client.calls))
        self.assertFalse(any((call['payload'] or {}).get('kind') == 'email' for call in self.client.calls))
        for path in MODULE.OUTPUT.rglob('*.json'):
            text = path.read_text(encoding='utf-8')
            for secret in (PASSWORD, TOKEN, INVITE_TOKEN, 'synthetic-refresh-token', 'must-not-load-admin', 'must-not-load-tunnel'):
                self.assertNotIn(secret, text)
        self.assertNotIn(INVITE_TOKEN, output)

    def test_invitation_revoke_runs_even_if_creation_fields_invalid(self):
        self.client.failed_creation = True
        code, _, report = self.run_main('--exercise')
        self.assertEqual(code, 1)
        self.assertEqual(self.client.invitation_state, 'revoked')
        self.assertFalse(any('/agent-chat/' in call['path'] for call in self.client.calls))

    def test_foreign_actual_reference_blocks_generation(self):
        self.client.foreign_source = True
        code, _, report = self.run_main('--exercise')
        self.assertEqual(code, 1)
        self.assertTrue(any(item.get('code') == 'reference_outside_source_knowledge' for item in report['checks']))
        self.assertEqual(len(self.client.sessions), 1)
        self.assertEqual(self.client.invitation_state, 'revoked')

    def test_failed_revoke_records_exact_id_for_manual_cleanup_without_token(self):
        self.client.failed_revoke = True
        code, output, report = self.run_main('--exercise')
        self.assertEqual(code, 1)
        self.assertTrue(any(item['name'] == 'invitation_cleanup' and item['invitation_id'] == 77 for item in report['manual_checks']))
        self.assertFalse(any('/agent-chat/' in call['path'] for call in self.client.calls))
        self.assertNotIn(INVITE_TOKEN, output + json.dumps(report))

    def test_review_block_does_not_retry_or_download_published_file(self):
        self.client.blocked = True
        code, _, report = self.run_main('--exercise')
        self.assertEqual(code, 1)
        self.assertEqual(report['artifacts'], [])
        self.assertEqual(len([call for call in self.client.calls if '/agent-chat/' in call['path']]), 2)
        self.assertEqual(len([call for call in self.client.calls if call['path'].endswith('/download')]), 1)

    def test_output_must_stay_private_and_refuse_overwrite(self):
        args = SimpleNamespace(output=self.directory / 'outside', exercise=False)
        with self.assertRaisesRegex(MODULE.BASE.AcceptanceError, 'private_output_directory_required'):
            MODULE.TrialRun(args, MODULE.partner_config(), self.client)
        args.output = MODULE.OUTPUT / 'one'
        args.output.mkdir(parents=True)
        with self.assertRaises(FileExistsError):
            MODULE.TrialRun(args, MODULE.partner_config(), self.client)

    def test_low_level_write_helpers_require_exercise(self):
        args = SimpleNamespace(output=None, exercise=False)
        run = MODULE.TrialRun(args, MODULE.partner_config(), self.client)
        for operation in (lambda: run.request('/api/v1/sessions', 'POST', {}), lambda: run.denial('write', '/api/v1/agents', 'POST', {}), run.invitation_cycle, run.exercise):
            with self.assertRaisesRegex(MODULE.BASE.AcceptanceError, 'exercise_required_for_mutation'):
                operation()
        self.assertEqual(self.client.calls, [])

    def test_invalid_write_probes_cannot_form_valid_mutations(self):
        self.run_main('--exercise')
        probes = [item for item in self.client.calls if item['method'] in {'POST', 'PUT'} and item['path'] != '/api/v1/auth/login' and '/agent-chat/' not in item['path'] and '/documents/' not in item['path'] and item['path'] != '/api/v1/sessions' and item['payload'] != {'kind': 'link'}]
        self.assertEqual(len(probes), 7)
        self.assertTrue(all(any(isinstance(value, list) for value in item['payload'].values()) for item in probes))

    def test_scrubbing_covers_secrets_nested_keys_and_url_tokens(self):
        run = MODULE.TrialRun(SimpleNamespace(output=None, exercise=False), MODULE.partner_config(), self.client)
        run.secrets = [PASSWORD, TOKEN]
        value = run.scrub({'email': 'never-save@example.test', 'data': [{'text': PASSWORD + ' ' + TOKEN + ' /register?token=opaque-secret&safe=1', 'token': INVITE_TOKEN}]})
        text = json.dumps(value)
        for secret in (PASSWORD, TOKEN, INVITE_TOKEN, 'opaque-secret', 'never-save@example.test'):
            self.assertNotIn(secret, text)

    def test_missing_proposal_gets_one_bounded_followup_then_normal_gates(self):
        self.client.skip_proposal_turns = 1
        code, _, report = self.run_main('--exercise')
        self.assertEqual(code, 2)
        self.assertEqual(report['document_followups'], 1)
        self.assertEqual(len(report['artifacts']), 1)
        self.assertTrue(any(call['payload'] == {'outline_hash': '0' * 64} for call in self.client.calls if call['path'].endswith('/confirm')))

    def test_no_proposal_stops_after_two_followups_without_confirmation(self):
        self.client.skip_proposal_turns = 10
        code, _, report = self.run_main('--exercise')
        self.assertEqual(code, 1)
        self.assertEqual(report['document_followups'], 2)
        self.assertEqual(len([call for call in self.client.calls if '/agent-chat/' in call['path']]), 4)
        self.assertFalse(any(call['path'].endswith('/confirm') for call in self.client.calls))

    def test_resume_reuses_original_session_and_never_repeats_invitation_or_mentor(self):
        previous = self.previous_report()
        code, _, report = self.run_main('--exercise', '--resume', str(previous))
        self.assertEqual(code, 2)
        self.assertEqual(report['mode'], 'document_resume')
        self.assertEqual(report['document_followups'], 1)
        self.assertEqual(len(self.client.sessions), 1)
        self.assertIsNone(self.client.invitation_state)
        chats = [call for call in self.client.calls if '/agent-chat/' in call['path']]
        self.assertEqual(len(chats), 1)
        self.assertEqual(chats[0]['payload']['agent_id'], AGENT_IDS['planner'])
        self.assertFalse(any(call['path'] == '/api/v1/sessions' and call['method'] == 'POST' for call in self.client.calls))

    def test_resume_does_not_reset_followup_budget(self):
        previous = self.previous_report(2)
        code, _, report = self.run_main('--exercise', '--resume', str(previous))
        self.assertEqual(code, 1)
        self.assertEqual(report['document_followups'], 2)
        self.assertFalse(any('/agent-chat/' in call['path'] for call in self.client.calls))

    def test_resume_published_only_downloads_existing_artifact(self):
        previous = self.previous_report()
        self.client.job = {'id': JOB_ID, 'state': 'published', 'format': 'docx', 'outline_hash': 'a' * 64, 'review': {'passed': True, 'issues': []}, 'artifact_sha256': hashlib.sha256(self.client.content).hexdigest()}
        code, _, report = self.run_main('--exercise', '--resume', str(previous))
        self.assertEqual(code, 2)
        self.assertEqual(len(report['artifacts']), 1)
        self.assertEqual([item['path'] for item in self.client.calls if item['method'] != 'GET'], ['/api/v1/auth/login'])

    def test_resume_requires_same_scope_and_explicit_exercise(self):
        previous = self.previous_report()
        code, _, _ = self.run_main('--resume', str(previous))
        self.assertEqual(code, 1)
        self.assertEqual(self.client.calls, [])
        path = previous / 'report.json'
        report = json.loads(path.read_text(encoding='utf-8'))
        report['tenant_id'] += 1
        path.write_text(json.dumps(report), encoding='utf-8')
        code, _, _ = self.run_main('--exercise', '--resume', str(previous))
        self.assertEqual(code, 1)
        self.assertEqual(self.client.calls, [])


if __name__ == '__main__':
    unittest.main()
