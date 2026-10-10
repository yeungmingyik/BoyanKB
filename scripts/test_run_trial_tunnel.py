from contextlib import contextmanager, redirect_stdout
import base64
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location('run_trial_tunnel', Path(__file__).with_name('run-trial-tunnel.py'))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
TUNNEL = '11111111-2222-4333-8444-555555555555'
TOKEN = base64.b64encode(json.dumps({'a': 'a' * 32, 't': TUNNEL, 's': base64.b64encode(b'x' * 32).decode()}).encode()).decode()


class FakeProcesses:
    def __init__(self, executable):
        self.identity_value = {'executable': MODULE.normalized_path(executable), 'creation_time': 133000000000000000}
        self.present = True
        self.running = True
        self.opened_calls = []
        self.identity_handles = []
        self.terminated = []

    @contextmanager
    def lock(self):
        yield

    @contextmanager
    def opened(self, pid, terminate=False):
        self.opened_calls.append((pid, terminate))
        yield 111 if self.present else None

    def active(self, handle):
        return self.running

    def identity(self, handle):
        self.identity_handles.append(handle)
        return copy.deepcopy(self.identity_value)

    def terminate(self, handle):
        self.terminated.append(handle)
        self.running = False


class TrialRunnerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        values = {'DIRECTORY': self.directory, 'ENV_FILE': self.directory / '.env', 'TUNNEL_STATE': self.directory / 'tunnel-state.json', 'PROCESS_STATE': self.directory / 'tunnel-process.json', 'CONFIG': self.directory / 'cloudflared-runner.yml', 'EXECUTABLE': self.directory / 'cloudflared.exe'}
        for name, value in values.items():
            item = patch.object(MODULE, name, value)
            item.start()
            self.addCleanup(item.stop)
        MODULE.EXECUTABLE.write_bytes(b'synthetic')
        MODULE.ENV_FILE.write_text('TRIAL_PUBLIC_HOST=' + MODULE.HOST + '\nTRIAL_TUNNEL_TOKEN=' + TOKEN + '\nOTHER_SECRET=not-for-child\n', encoding='utf-8')
        self.prepared = {'schema_version': 1, 'host': MODULE.HOST, 'account_id': 'a' * 32, 'zone_id': 'b' * 32, 'tunnel_id': TUNNEL, 'prepared': True, 'original_dns': [], 'original_dns_fingerprint': hashlib.sha256(b'[]').hexdigest()}
        MODULE.TUNNEL_STATE.write_text(json.dumps(self.prepared), encoding='utf-8')
        self.processes = FakeProcesses(MODULE.EXECUTABLE)
        self.child = SimpleNamespace(pid=4242, _handle=222)

    def process_state(self):
        state = {'schema_version': 1, 'host': MODULE.HOST, 'tunnel_id': TUNNEL, 'pid': 4242, **self.processes.identity_value, 'metrics': MODULE.METRICS}
        MODULE.save_process(state)
        return state

    def start(self, **kwargs):
        with patch.object(MODULE, 'gateway_ready'), patch.object(MODULE, 'metrics_port_free'), patch.object(MODULE, 'metrics_ready', return_value=True), patch.object(MODULE.subprocess, 'Popen', return_value=self.child, **kwargs) as spawn:
            result = MODULE.start(self.processes)
        return result, spawn

    def test_default_is_read_only_status_without_environment_or_network(self):
        output = io.StringIO()
        with patch.object(MODULE, 'WindowsProcesses', return_value=self.processes), patch.object(MODULE, 'tunnel_token', side_effect=AssertionError('secret read')), patch.object(MODULE, 'gateway_ready', side_effect=AssertionError('network')), patch.object(MODULE.subprocess, 'Popen', side_effect=AssertionError('spawn')), redirect_stdout(output):
            self.assertEqual(MODULE.main([]), 0)
        self.assertEqual(json.loads(output.getvalue()), {'status': 'NOT_STARTED', 'ready': False})
        self.assertFalse(MODULE.PROCESS_STATE.exists())
        self.assertFalse(MODULE.CONFIG.exists())

    def test_start_token_only_in_minimal_child_environment_no_window_or_output(self):
        with patch.dict(MODULE.os.environ, {'SystemRoot': 'C:/Windows', 'TEMP': 'C:/Temp', 'TUNNEL_LOGFILE': 'leak.log', 'TRIAL_CLOUDFLARE_API_TOKEN': 'private', 'HTTPS_PROXY': 'http://proxy', 'OTHER_SECRET': 'secret'}, clear=True):
            result, spawn = self.start()
        self.assertEqual(result['status'], 'RUNNING')
        args, options = spawn.call_args
        command = args[0]
        self.assertNotIn(TOKEN, ' '.join(command))
        self.assertNotIn('--token', command)
        self.assertEqual(command[0], str(MODULE.EXECUTABLE))
        self.assertEqual(command[command.index('--metrics') + 1], '127.0.0.1:49312')
        self.assertEqual(command[-1], 'run')
        self.assertIn('--management-diagnostics=false', command)
        self.assertEqual({key.upper(): value for key, value in options['env'].items()}, {'SYSTEMROOT': 'C:/Windows', 'TEMP': 'C:/Temp', 'TUNNEL_TOKEN': TOKEN})
        self.assertEqual(options['creationflags'], MODULE.CREATE_NO_WINDOW)
        for name in ('stdin', 'stdout', 'stderr'):
            self.assertEqual(options[name], subprocess.DEVNULL)
        self.assertFalse(options['shell'])
        self.assertTrue(options['close_fds'])
        self.assertEqual(MODULE.CONFIG.read_bytes(), b'{}\n')
        self.assertNotIn(TOKEN, MODULE.PROCESS_STATE.read_text())
        self.assertEqual(self.processes.identity_handles, [222])

    def test_success_output_never_contains_token(self):
        output = io.StringIO()
        with patch.object(MODULE, 'WindowsProcesses', return_value=self.processes), patch.object(MODULE, 'gateway_ready'), patch.object(MODULE, 'metrics_port_free'), patch.object(MODULE, 'metrics_ready', return_value=True), patch.object(MODULE.subprocess, 'Popen', return_value=self.child), redirect_stdout(output):
            self.assertEqual(MODULE.main(['start']), 0)
        self.assertNotIn(TOKEN, output.getvalue())
        self.assertNotIn('TUNNEL_TOKEN', output.getvalue())

    def test_start_requires_prepared_host_scope_and_fingerprint(self):
        for update in ({'prepared': False}, {'host': 'other.example.com'}, {'tunnel_id': '../other'}, {'account_id': 'bad'}, {'original_dns_fingerprint': 'bad'}, {'original_dns': [{'name': 'other.example.com'}]}):
            with self.subTest(update=update):
                MODULE.TUNNEL_STATE.write_text(json.dumps({**self.prepared, **update}))
                with patch.object(MODULE, 'tunnel_token', side_effect=AssertionError('secret read')), patch.object(MODULE.subprocess, 'Popen', side_effect=AssertionError('spawn')):
                    with self.assertRaises(MODULE.RunnerError):
                        MODULE.start(self.processes)

    def test_gateway_check_precedes_secret_and_spawn(self):
        with patch.object(MODULE, 'gateway_ready', side_effect=MODULE.RunnerError('gateway_auth_not_ready')), patch.object(MODULE, 'tunnel_token', side_effect=AssertionError('secret read')), patch.object(MODULE.subprocess, 'Popen', side_effect=AssertionError('spawn')):
            with self.assertRaisesRegex(MODULE.RunnerError, 'gateway_auth_not_ready'):
                MODULE.start(self.processes)

    def test_gateway_requires_invite_only_and_boolean_complex_password(self):
        for payload in ({'registration_mode': 'open', 'complex_password_enabled': True}, {'registration_mode': 'invite_only', 'complex_password_enabled': 'true'}, [], {}):
            with self.subTest(payload=payload), patch.object(MODULE, 'local_response', return_value=json.dumps(payload).encode()):
                with self.assertRaises(MODULE.RunnerError):
                    MODULE.gateway_ready()
        with patch.object(MODULE, 'local_response', return_value=b'{"registration_mode":"invite_only","complex_password_enabled":true}') as call:
            MODULE.gateway_ready()
        self.assertEqual(call.call_args.args[0], 'http://127.0.0.1:8090/api/v1/auth/config')
        self.assertEqual(call.call_args.args[1], {'Host': MODULE.HOST})

    def test_metrics_conflict_refuses_spawn_before_secret(self):
        with patch.object(MODULE, 'gateway_ready'), patch.object(MODULE, 'metrics_port_free', side_effect=MODULE.RunnerError('metrics_port_in_use')), patch.object(MODULE, 'tunnel_token', side_effect=AssertionError('secret read')), patch.object(MODULE.subprocess, 'Popen', side_effect=AssertionError('spawn')):
            with self.assertRaisesRegex(MODULE.RunnerError, 'metrics_port_in_use'):
                MODULE.start(self.processes)

    def test_environment_rejects_duplicates_placeholder_or_wrong_host(self):
        values = [f'TRIAL_PUBLIC_HOST={MODULE.HOST}\nTRIAL_TUNNEL_TOKEN={TOKEN}\nTRIAL_TUNNEL_TOKEN={TOKEN}\n', 'TRIAL_PUBLIC_HOST=other.example.com\nTRIAL_TUNNEL_TOKEN=' + TOKEN, 'TRIAL_PUBLIC_HOST=' + MODULE.HOST + '\nTRIAL_TUNNEL_TOKEN=REPLACE_WITH_TUNNEL_TOKEN']
        for value in values:
            with self.subTest(value=value):
                MODULE.ENV_FILE.write_text(value)
                with self.assertRaises(MODULE.RunnerError):
                    MODULE.tunnel_token()

    def test_config_drift_refuses_spawn(self):
        MODULE.CONFIG.write_text('logfile: leak.log\n')
        with patch.object(MODULE, 'gateway_ready'), patch.object(MODULE, 'metrics_port_free'), patch.object(MODULE.subprocess, 'Popen', side_effect=AssertionError('spawn')):
            with self.assertRaisesRegex(MODULE.RunnerError, 'runner_config_changed'):
                MODULE.start(self.processes)

    def test_running_start_is_idempotent_without_secret_read_or_new_process(self):
        self.process_state()
        with patch.object(MODULE, 'metrics_ready', return_value=True), patch.object(MODULE, 'tunnel_token', side_effect=AssertionError('secret read')), patch.object(MODULE.subprocess, 'Popen', side_effect=AssertionError('spawn')):
            self.assertEqual(MODULE.start(self.processes)['status'], 'RUNNING')

    def test_dead_record_can_restart_with_new_process_identity(self):
        self.process_state()
        self.processes.present = False
        self.processes.identity_value['creation_time'] += 1
        result, spawn = self.start()
        self.assertEqual(result['status'], 'RUNNING')
        spawn.assert_called_once()
        self.assertEqual(MODULE.load_process()['creation_time'], self.processes.identity_value['creation_time'])

    def test_reused_pid_or_wrong_executable_never_stopped_or_restarted(self):
        state = self.process_state()
        for update in ({'creation_time': state['creation_time'] + 1}, {'executable': str(self.directory / 'other.exe')}):
            with self.subTest(update=update):
                self.processes.identity_value = {key: state[key] for key in ('executable', 'creation_time')}
                self.processes.identity_value.update(update)
                with self.assertRaisesRegex(MODULE.RunnerError, 'process_identity_mismatch'):
                    MODULE.stop(self.processes)
                with patch.object(MODULE.subprocess, 'Popen', side_effect=AssertionError('spawn')):
                    with self.assertRaisesRegex(MODULE.RunnerError, 'process_identity_mismatch'):
                        MODULE.start(self.processes)
        self.assertEqual(self.processes.terminated, [])

    def test_stop_verifies_and_terminates_same_handle_without_secret(self):
        self.process_state()
        with patch.object(MODULE, 'tunnel_token', side_effect=AssertionError('secret read')), patch.object(MODULE, 'metrics_ready', side_effect=AssertionError('network')):
            result = MODULE.stop(self.processes)
        self.assertEqual(result['status'], 'STOPPED')
        self.assertEqual(self.processes.opened_calls, [(4242, True)])
        self.assertEqual(self.processes.identity_handles, [111])
        self.assertEqual(self.processes.terminated, [111])

    def test_stop_missing_or_exited_is_idempotent(self):
        self.assertEqual(MODULE.stop(self.processes)['status'], 'NOT_STARTED')
        self.process_state()
        self.processes.present = False
        self.assertEqual(MODULE.stop(self.processes)['status'], 'STOPPED')
        self.processes.present = True
        self.processes.running = False
        self.assertEqual(MODULE.stop(self.processes)['status'], 'STOPPED')
        self.assertEqual(self.processes.terminated, [])

    def test_invalid_saved_scope_or_types_fail_closed(self):
        state = self.process_state()
        for update in ({'host': 'other.example.com'}, {'metrics': '0.0.0.0:49312'}, {'pid': True}, {'pid': -1}, {'creation_time': '123'}, {'executable': str(self.directory / 'other.exe')}, {'unexpected': 'value'}):
            with self.subTest(update=update):
                MODULE.PROCESS_STATE.write_text(json.dumps({**state, **update}))
                with self.assertRaises(MODULE.RunnerError):
                    MODULE.stop(self.processes)
        self.assertEqual(self.processes.terminated, [])

    def test_persist_failure_stops_only_new_child_handle(self):
        with patch.object(MODULE, 'gateway_ready'), patch.object(MODULE, 'metrics_port_free'), patch.object(MODULE.subprocess, 'Popen', return_value=self.child), patch.object(MODULE, 'save_process', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                MODULE.start(self.processes)
        self.assertEqual(self.processes.terminated, [222])

    def test_readiness_timeout_preserves_managed_process_for_status_stop(self):
        with patch.object(MODULE, 'gateway_ready'), patch.object(MODULE, 'metrics_port_free'), patch.object(MODULE.subprocess, 'Popen', return_value=self.child), patch.object(MODULE, 'metrics_ready', return_value=False), patch.object(MODULE.time, 'monotonic', side_effect=[0, 1, 31]), patch.object(MODULE.time, 'sleep'):
            result = MODULE.start(self.processes)
        self.assertEqual(result['status'], 'STARTING_OR_DISCONNECTED')
        self.assertFalse(result['ready'])
        self.assertEqual(MODULE.load_process()['pid'], 4242)
        self.assertEqual(self.processes.terminated, [])

    def test_raw_spawn_exception_is_sanitized(self):
        output = io.StringIO()
        with patch.object(MODULE, 'WindowsProcesses', return_value=self.processes), patch.object(MODULE, 'gateway_ready'), patch.object(MODULE, 'metrics_port_free'), patch.object(MODULE.subprocess, 'Popen', side_effect=OSError(TOKEN)), redirect_stdout(output):
            self.assertEqual(MODULE.main(['start']), 1)
        self.assertEqual(json.loads(output.getvalue())['code'], 'cloudflared_start_failed')
        self.assertNotIn(TOKEN, output.getvalue())

    def test_transport_disallows_redirects(self):
        with self.assertRaisesRegex(MODULE.RunnerError, 'local_redirect_refused'):
            MODULE.NoRedirect().redirect_request(None, None, 302, None, None, 'https://other.example.com')

    def test_local_transport_ignores_environment_proxies_and_bounds_response(self):
        response = SimpleNamespace(status=200, read=lambda limit: b'{}')
        class ResponseContext:
            def __enter__(self):
                return response

            def __exit__(self, *args):
                return False
        opener = SimpleNamespace(open=lambda request, timeout: ResponseContext())
        with patch.object(MODULE.urllib.request, 'build_opener', return_value=opener) as build:
            self.assertEqual(MODULE.local_response('http://127.0.0.1:49312/ready'), b'{}')
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1], MODULE.NoRedirect)
        response.read = lambda limit: b'x' * 65537
        with patch.object(MODULE.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaisesRegex(MODULE.RunnerError, 'local_response_too_large'):
                MODULE.local_response('http://127.0.0.1:49312/ready')

    def test_new_tunnel_id_cannot_adopt_recorded_process(self):
        self.process_state()
        self.prepared['tunnel_id'] = '99999999-2222-4333-8444-555555555555'
        MODULE.TUNNEL_STATE.write_text(json.dumps(self.prepared))
        with patch.object(MODULE.subprocess, 'Popen', side_effect=AssertionError('spawn')):
            with self.assertRaisesRegex(MODULE.RunnerError, 'process_tunnel_mismatch'):
                MODULE.start(self.processes)

    def test_token_account_tunnel_or_endpoint_cannot_override_prepared_scope(self):
        payload = json.loads(base64.b64decode(TOKEN))
        for update in ({'a': 'f' * 32}, {'t': '99999999-2222-4333-8444-555555555555'}, {'e': 'unexpected.example.com'}, {'s': 'invalid'}):
            with self.subTest(update=update):
                token = base64.b64encode(json.dumps({**payload, **update}).encode()).decode()
                with self.assertRaises(MODULE.RunnerError):
                    MODULE.validate_token_scope(token, self.prepared)
        MODULE.validate_token_scope(TOKEN, self.prepared)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows identity API')
    def test_windows_wrapper_reads_only_own_process_identity(self):
        processes = MODULE.WindowsProcesses()
        with processes.opened(os.getpid()) as handle:
            self.assertTrue(processes.active(handle))
            identity = processes.identity(handle)
        self.assertEqual(identity['executable'], MODULE.normalized_path(sys.executable))
        self.assertGreater(identity['creation_time'], 0)


if __name__ == '__main__':
    unittest.main()
