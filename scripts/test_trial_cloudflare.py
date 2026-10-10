import copy
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request


SPEC = importlib.util.spec_from_file_location('trial_cloudflare', Path(__file__).with_name('trial-cloudflare.py'))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
ZONE = 'a' * 32
ACCOUNT = 'b' * 32
RECORD = 'c' * 32
TUNNEL = '11111111-2222-4333-8444-555555555555'
TOKEN = 'synthetic-token-value-for-offline-tests'
TUNNEL_TOKEN = 'c3ludGhldGljLXR1bm5lbC10b2tlbi1mb3ItdGVzdHM='


def dns_record():
    return {'id': RECORD, 'name': MODULE.HOST, 'type': 'CNAME', 'content': '99999999-2222-4333-8444-555555555555.cfargotunnel.com', 'proxied': True, 'ttl': 1}


class FakeCloudflare:
    def __init__(self):
        self.calls = []
        self.records = [dns_record()]
        self.tunnels = []
        self.config = {'ingress': [], 'warp-routing': {'enabled': False}}
        self.connector = {'id': TUNNEL, 'status': 'healthy', 'connections': [{'id': 'connection', 'is_pending_reconnect': False}]}
        self.before_call = None
        self.write_result = None

    def call(self, path, method='GET', body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        if self.before_call:
            self.before_call(path, method)
        if path.startswith('/zones?'):
            result = [{'id': ZONE, 'account': {'id': ACCOUNT}, 'name': MODULE.DOMAIN, 'status': 'active'}]
        elif path.startswith('/zones/' + ZONE + '/dns_records'):
            if method == 'GET':
                result = self.records
            else:
                self.records = [{'id': RECORD, **body}]
                result = self.write_result or self.records[0]
        elif path.startswith('/accounts/' + ACCOUNT + '/cfd_tunnel?'):
            result = self.tunnels
        elif path == '/accounts/' + ACCOUNT + '/cfd_tunnel' and method == 'POST':
            self.tunnels = [{'id': TUNNEL, 'name': MODULE.TUNNEL_NAME, 'config_src': 'cloudflare'}]
            result = self.tunnels[0]
        elif path == '/accounts/' + ACCOUNT + '/cfd_tunnel/' + TUNNEL:
            result = self.connector
        elif path == '/accounts/' + ACCOUNT + '/cfd_tunnel/' + TUNNEL + '/configurations':
            if method == 'PUT':
                self.config = copy.deepcopy(body['config'])
            result = {'config': self.config}
        elif path == '/accounts/' + ACCOUNT + '/cfd_tunnel/' + TUNNEL + '/token':
            result = TUNNEL_TOKEN
        else:
            raise AssertionError((method, path))
        return copy.deepcopy(result)

    def writes(self):
        return [call for call in self.calls if call[0] != 'GET']


class TrialCloudflareTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.env = self.root / '.env'
        self.env.write_text('TRIAL_PUBLIC_HOST=' + MODULE.HOST + '\nTRIAL_CLOUDFLARE_API_TOKEN=' + TOKEN + '\nOTHER_SETTING=preserved\n', encoding='utf-8')
        self.state = self.root / 'tunnel-state.json'
        state_patch = patch.object(MODULE, 'STATE', self.state)
        state_patch.start()
        self.addCleanup(state_patch.stop)
        self.client = FakeCloudflare()

    def prepared(self):
        snapshot = MODULE.inspect(self.client)
        with patch.object(MODULE, 'gateway_ready'):
            result = MODULE.prepare(self.client, snapshot, self.env, snapshot['dns_fingerprint'])
        self.assertEqual(result['status'], 'PREPARED')
        self.assertFalse(result['dns_changed'])
        self.client.calls.clear()
        return MODULE.inspect(self.client)

    def publish(self, snapshot):
        with patch.object(MODULE, 'gateway_ready'):
            return MODULE.publish(self.client, snapshot)

    def test_default_inspect_has_no_remote_or_local_writes(self):
        output = io.StringIO()
        original = self.env.read_bytes()
        with patch.object(MODULE, 'Cloudflare', return_value=self.client), patch.object(MODULE, 'gateway_ready', side_effect=AssertionError('gateway unused')), redirect_stdout(output):
            result = MODULE.main(['--env-file', str(self.env)])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())['status'], 'READ_ONLY')
        self.assertEqual(self.client.writes(), [])
        self.assertEqual(self.env.read_bytes(), original)
        self.assertFalse(self.state.exists())
        self.assertNotIn(TOKEN, output.getvalue())

    def test_transport_disables_proxies_and_refuses_redirects(self):
        with patch.dict(MODULE.os.environ, {'HTTPS_PROXY': 'http://untrusted.invalid:8080'}):
            client = MODULE.Cloudflare(TOKEN)
        self.assertFalse(any(isinstance(handler, urllib.request.ProxyHandler) and handler.proxies for handler in client.opener.handlers))
        handler = next(handler for handler in client.opener.handlers if isinstance(handler, MODULE.NoRedirect))
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code), self.assertRaisesRegex(MODULE.TunnelError, 'redirect_refused'):
                handler.redirect_request(None, None, code, '', {}, 'https://untrusted.invalid')

    def test_incomplete_api_pages_and_rejected_api_messages_fail_closed(self):
        client = MODULE.Cloudflare(TOKEN)
        cases = [({'success': True, 'result': [], 'result_info': {'total_pages': 2}}, 'cloudflare_response_incomplete'), ({'success': True, 'result': [1], 'result_info': {'total_count': 2}}, 'cloudflare_response_incomplete'), ({'success': False, 'errors': [{'message': TOKEN}]}, 'cloudflare_api_rejected')]
        for response, code in cases:
            with self.subTest(code=code), patch.object(MODULE, 'request_json', return_value=response):
                with self.assertRaisesRegex(MODULE.TunnelError, code) as failure:
                    client.call('/zones')
                self.assertNotIn(TOKEN, str(failure.exception))

    def test_gateway_requires_invitation_and_complex_password(self):
        for config in ({}, {'registration_mode': 'open', 'complex_password_enabled': True}, {'registration_mode': 'invite_only', 'complex_password_enabled': False}, {'registration_mode': 'invite_only', 'complex_password_enabled': 'true'}):
            with self.subTest(config=config), patch.object(MODULE, 'request_json', return_value=config):
                with self.assertRaisesRegex(MODULE.TunnelError, 'gateway_auth_not_ready'):
                    MODULE.gateway_ready()
        with patch.object(MODULE, 'request_json', return_value={'registration_mode': 'invite_only', 'complex_password_enabled': True}) as request:
            MODULE.gateway_ready()
            self.assertEqual(request.call_args.args[1], 'http://127.0.0.1:8090/api/v1/auth/config')
            self.assertEqual(request.call_args.kwargs['headers'], {'Host': MODULE.HOST})

    def test_prepare_auth_or_fingerprint_failure_never_mutates_cloudflare(self):
        snapshot = MODULE.inspect(self.client)
        for fingerprint, failure in ((snapshot['dns_fingerprint'], 'gateway_auth_not_ready'), ('0' * 64, 'dns_changed_since_review')):
            with self.subTest(failure=failure), patch.object(MODULE, 'gateway_ready', side_effect=MODULE.TunnelError('gateway_auth_not_ready')):
                with self.assertRaisesRegex(MODULE.TunnelError, failure):
                    MODULE.prepare(self.client, snapshot, self.env, fingerprint)
                self.assertEqual(self.client.writes(), [])
                self.assertFalse(self.state.exists())

    def test_prepare_rejects_multiple_records_and_unmanaged_tunnel(self):
        for mutation in ('records', 'tunnel'):
            client = FakeCloudflare()
            if mutation == 'records':
                client.records.append({**dns_record(), 'id': 'd' * 32, 'type': 'A', 'content': '192.0.2.2'})
            else:
                client.tunnels = [{'id': TUNNEL, 'name': MODULE.TUNNEL_NAME}]
            snapshot = MODULE.inspect(client)
            with self.subTest(mutation=mutation), patch.object(MODULE, 'gateway_ready'):
                with self.assertRaises(MODULE.TunnelError):
                    MODULE.prepare(client, snapshot, self.env, snapshot['dns_fingerprint'])
                self.assertEqual(client.writes(), [])

    def test_prepare_keeps_token_only_in_env_and_reuses_owned_tunnel(self):
        snapshot = self.prepared()
        self.assertIn('TRIAL_TUNNEL_TOKEN=' + TUNNEL_TOKEN, self.env.read_text())
        self.assertIn('OTHER_SETTING=preserved', self.env.read_text())
        self.assertNotIn(TUNNEL_TOKEN, self.state.read_text())
        self.assertNotIn('tunnel_secret', self.state.read_text())
        self.assertEqual(self.client.records, [dns_record()])
        with patch.object(MODULE, 'gateway_ready'):
            MODULE.prepare(self.client, snapshot, self.env, snapshot['dns_fingerprint'])
        self.assertEqual(self.client.writes(), [])
        self.assertFalse(self.env.with_name('.env.pending').exists())

    def test_prepare_requires_env_destination_before_remote_creation(self):
        other = self.root / 'public-config.txt'
        other.write_text(self.env.read_text())
        snapshot = MODULE.inspect(self.client)
        with self.assertRaisesRegex(MODULE.TunnelError, 'tunnel_environment_file_required'):
            MODULE.prepare(self.client, snapshot, other, snapshot['dns_fingerprint'])
        self.assertEqual(self.client.writes(), [])

    def test_inspection_refuses_other_hostname_or_identifier_path_injection(self):
        for field, value in (('name', 'unrelated.' + MODULE.DOMAIN), ('id', '../other'), ('type', 'TXT')):
            self.client.records = [{**dns_record(), field: value}]
            with self.subTest(field=field), self.assertRaises(MODULE.TunnelError):
                MODULE.inspect(self.client)
        self.assertEqual(self.client.writes(), [])

    def test_state_scope_identifier_and_fingerprint_fail_before_publish(self):
        snapshot = self.prepared()
        original = json.loads(self.state.read_text())
        for field, value in (('host', 'other.invalid'), ('zone_id', 'd' * 32), ('tunnel_id', '../elsewhere'), ('original_dns_fingerprint', '0' * 64), ('prepared', 'yes')):
            self.state.write_text(json.dumps({**original, field: value}))
            with self.subTest(field=field), self.assertRaises(MODULE.TunnelError):
                self.publish(snapshot)
            self.assertEqual(self.client.writes(), [])

    def test_publish_requires_healthy_nonpending_connector(self):
        snapshot = self.prepared()
        for connector in ({'status': 'down'}, {'status': 'degraded'}, {'connections': []}, {'connections': [{'id': 'pending', 'is_pending_reconnect': True}]}, {'id': '22222222-2222-4333-8444-555555555555'}):
            original = self.client.connector
            self.client.connector = {**original, **connector}
            with self.subTest(connector=connector), self.assertRaisesRegex(MODULE.TunnelError, 'tunnel_connector_not_ready'):
                self.publish(snapshot)
            self.assertEqual(self.client.writes(), [])
            self.client.connector = original

    def test_publish_rechecks_gateway_auth_before_any_remote_mutation(self):
        snapshot = self.prepared()
        with patch.object(MODULE, 'gateway_ready', side_effect=MODULE.TunnelError('gateway_auth_not_ready')):
            with self.assertRaisesRegex(MODULE.TunnelError, 'gateway_auth_not_ready'):
                MODULE.publish(self.client, snapshot)
        self.assertEqual(self.client.writes(), [])
        self.assertFalse(json.loads(self.state.read_text())['published'])

    def test_publish_rechecks_routing_and_disallows_private_network_routing(self):
        snapshot = self.prepared()
        for config in ({'ingress': [{'hostname': 'other.invalid', 'service': 'http://127.0.0.1:9000'}]}, {**MODULE.tunnel_config(), 'warp-routing': {'enabled': True}}, {**MODULE.tunnel_config(), 'originRequest': {'proxyAddress': 'other.invalid'}}):
            self.client.config = config
            with self.subTest(config=config), self.assertRaisesRegex(MODULE.TunnelError, 'tunnel_routes_changed'):
                self.publish(snapshot)
            self.assertEqual(self.client.writes(), [])

    def test_publish_rejects_dns_change_since_review_or_during_health_check(self):
        snapshot = self.prepared()
        self.client.records[0]['content'] = 'edited.example.org'
        changed = MODULE.inspect(self.client)
        with self.assertRaisesRegex(MODULE.TunnelError, 'dns_changed_since_review'):
            self.publish(changed)
        with self.assertRaisesRegex(MODULE.TunnelError, 'dns_changed_during_publish'):
            self.publish(snapshot)
        self.assertEqual(self.client.writes(), [])

    def test_publish_modifies_only_reviewed_cname_and_is_idempotent(self):
        snapshot = self.prepared()
        result = self.publish(snapshot)
        self.assertTrue(result['changed'])
        writes = self.client.writes()
        self.assertEqual(len(writes), 1)
        method, path, body = writes[0]
        self.assertEqual((method, path), ('PUT', '/zones/' + ZONE + '/dns_records/' + RECORD))
        self.assertEqual(body, {'type': 'CNAME', 'name': MODULE.HOST, 'content': TUNNEL + '.cfargotunnel.com', 'proxied': True, 'ttl': 1})
        self.client.calls.clear()
        result = self.publish(MODULE.inspect(self.client))
        self.assertFalse(result['changed'])
        self.assertEqual(self.client.writes(), [])
        self.assertTrue(json.loads(self.state.read_text())['published'])

    def test_publish_creates_only_exact_hostname_when_no_dns_exists(self):
        self.client.records = []
        result = self.publish(self.prepared())
        self.assertTrue(result['changed'])
        method, path, body = self.client.writes()[0]
        self.assertEqual((method, path), ('POST', '/zones/' + ZONE + '/dns_records'))
        self.assertEqual(body['name'], MODULE.HOST)

    def test_dns_write_requires_verified_response_and_readback(self):
        snapshot = self.prepared()
        self.client.write_result = {**dns_record(), 'name': 'other.invalid'}
        with self.assertRaises(MODULE.TunnelError):
            self.publish(snapshot)
        self.assertFalse(json.loads(self.state.read_text())['published'])
        self.assertEqual(len(self.client.writes()), 1)

    def test_dns_write_readback_race_is_not_reported_as_published(self):
        snapshot = self.prepared()
        reads = 0
        def changed(path, method):
            nonlocal reads
            if method == 'GET' and '/dns_records?' in path:
                reads += 1
                if reads == 2:
                    self.client.records[0]['content'] = 'changed-after-update.example.org'
        self.client.before_call = changed
        with self.assertRaisesRegex(MODULE.TunnelError, 'dns_publish_unverified'):
            self.publish(snapshot)
        self.assertFalse(json.loads(self.state.read_text())['published'])
        self.assertEqual(len(self.client.writes()), 1)

    def test_http_errors_do_not_expose_response_body_or_token(self):
        error = urllib.error.HTTPError('https://api.cloudflare.com/', 403, TOKEN, {}, io.BytesIO(TUNNEL_TOKEN.encode()))
        opener = unittest.mock.Mock()
        opener.open.side_effect = error
        with self.assertRaisesRegex(MODULE.TunnelError, '^http_403$') as failure:
            MODULE.request_json(opener, 'https://api.cloudflare.com/')
        self.assertNotIn(TOKEN, str(failure.exception))
        output = io.StringIO()
        with patch.object(MODULE, 'request_json', side_effect=MODULE.TunnelError('http_403')), redirect_stdout(output):
            code = MODULE.main(['--env-file', str(self.env)])
        self.assertEqual(code, 1)
        self.assertNotIn(TOKEN, output.getvalue())
        self.assertNotIn(TUNNEL_TOKEN, output.getvalue())


if __name__ == '__main__':
    unittest.main()
