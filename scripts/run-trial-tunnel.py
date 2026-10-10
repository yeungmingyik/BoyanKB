import argparse
import base64
import binascii
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'temp/enterprise-trial'
ENV_FILE = DIRECTORY / '.env'
TUNNEL_STATE = DIRECTORY / 'tunnel-state.json'
PROCESS_STATE = DIRECTORY / 'tunnel-process.json'
CONFIG = DIRECTORY / 'cloudflared-runner.yml'
EXECUTABLE = Path('C:/Users/Yeung/scoop/apps/cloudflared/current/cloudflared.exe')
HOST = 'kb.boyantech.xyz'
METRICS = '127.0.0.1:49312'
CREATE_NO_WINDOW = 0x08000000
WAIT_OBJECT_0 = 0
WAIT_ABANDONED = 0x80
WAIT_TIMEOUT = 258


class RunnerError(Exception):
    pass


def require(condition, code):
    if not condition:
        raise RunnerError(code)


def normalized_path(value):
    return os.path.normcase(str(Path(value).resolve()))


def identifier(value):
    try:
        require(isinstance(value, str) and str(uuid.UUID(value)) == value, 'invalid_tunnel_id')
    except ValueError:
        raise RunnerError('invalid_tunnel_id') from None
    return value


def read_json(path):
    require(path.is_file() and not path.is_symlink(), 'state_file_required')
    require(path.stat().st_size <= 65536, 'state_file_too_large')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise RunnerError('state_file_invalid') from None
    require(isinstance(value, dict), 'state_file_invalid')
    return value


def prepared_state():
    state = read_json(TUNNEL_STATE)
    require(state.get('schema_version') == 1 and state.get('host') == HOST and state.get('prepared') is True, 'tunnel_not_prepared')
    identifier(state.get('tunnel_id'))
    require(all(isinstance(state.get(key), str) and re.fullmatch(r'[a-f0-9]{32}', state[key]) for key in ('account_id', 'zone_id')), 'tunnel_scope_invalid')
    records = state.get('original_dns')
    require(isinstance(records, list) and len(records) <= 1, 'saved_dns_invalid')
    for record in records:
        require(isinstance(record, dict) and set(record) == {'id', 'name', 'type', 'content', 'proxied', 'ttl'}, 'saved_dns_invalid')
        require(record['name'] == HOST and record['type'] in {'A', 'AAAA', 'CNAME'} and isinstance(record['content'], str), 'saved_dns_invalid')
        require(isinstance(record['id'], str) and re.fullmatch(r'[a-f0-9]{32}', record['id']) and type(record['proxied']) is bool and type(record['ttl']) is int, 'saved_dns_invalid')
    digest = hashlib.sha256(json.dumps(records, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    require(state.get('original_dns_fingerprint') == digest, 'saved_dns_fingerprint_invalid')
    return state


def load_process():
    if not PROCESS_STATE.exists():
        return None
    state = read_json(PROCESS_STATE)
    require(set(state) == {'schema_version', 'host', 'tunnel_id', 'pid', 'executable', 'creation_time', 'metrics'}, 'process_state_invalid')
    require(state['schema_version'] == 1 and state['host'] == HOST and state['metrics'] == METRICS, 'process_scope_mismatch')
    identifier(state['tunnel_id'])
    require(type(state['pid']) is int and 0 < state['pid'] < 2 ** 32 and isinstance(state['executable'], str), 'process_state_invalid')
    require(type(state['creation_time']) is int and state['creation_time'] > 0, 'process_state_invalid')
    require(normalized_path(state['executable']) == normalized_path(EXECUTABLE), 'process_executable_changed')
    return state


def save_process(state):
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    temporary = PROCESS_STATE.with_suffix('.pending')
    with temporary.open('x', encoding='utf-8', newline='\n') as handle:
        json.dump(state, handle, indent=2)
        handle.write('\n')
    os.replace(temporary, PROCESS_STATE)


def tunnel_token():
    require(ENV_FILE.is_file() and not ENV_FILE.is_symlink() and ENV_FILE.stat().st_size <= 1024 * 1024, 'tunnel_environment_required')
    values = {}
    for line in ENV_FILE.read_text(encoding='utf-8-sig').splitlines():
        key, separator, value = line.partition('=')
        key = key.strip()
        if separator and key in {'TRIAL_PUBLIC_HOST', 'TRIAL_TUNNEL_TOKEN'}:
            require(key not in values, 'duplicate_environment_key')
            values[key] = value.strip().strip('\"\'')
    require(values.get('TRIAL_PUBLIC_HOST') == HOST, 'unexpected_public_host')
    token = values.get('TRIAL_TUNNEL_TOKEN', '')
    require(not token.startswith('REPLACE_') and re.fullmatch(r'[A-Za-z0-9_+/=-]{30,8192}', token), 'tunnel_token_missing')
    return token


def child_environment(token):
    allowed = {'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'TEMP', 'TMP'}
    result = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    result['TUNNEL_TOKEN'] = token
    return result


def validate_token_scope(token, state):
    try:
        value = json.loads(base64.b64decode(token, validate=True))
        require(isinstance(value, dict) and value.get('a') == state['account_id'] and value.get('t') == state['tunnel_id'] and not value.get('e'), 'tunnel_token_scope_mismatch')
        require(isinstance(value.get('s'), str) and len(base64.b64decode(value['s'], validate=True)) == 32, 'tunnel_token_invalid')
    except (ValueError, binascii.Error):
        raise RunnerError('tunnel_token_invalid') from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RunnerError('local_redirect_refused')


def local_response(url, headers=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with opener.open(request, timeout=3) as response:
            require(response.status == 200, 'local_service_unavailable')
            body = response.read(65537)
            require(len(body) <= 65536, 'local_response_too_large')
            return body
    except (urllib.error.URLError, OSError, TimeoutError):
        raise RunnerError('local_service_unavailable') from None


def gateway_ready():
    try:
        value = json.loads(local_response('http://127.0.0.1:8090/api/v1/auth/config', {'Host': HOST}))
    except ValueError:
        raise RunnerError('gateway_response_invalid') from None
    require(isinstance(value, dict) and value.get('registration_mode') == 'invite_only' and value.get('complex_password_enabled') is True, 'gateway_auth_not_ready')


def metrics_ready():
    try:
        local_response('http://' + METRICS + '/ready')
        return True
    except RunnerError:
        return False


def metrics_port_free():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(('127.0.0.1', 49312))
    except OSError:
        raise RunnerError('metrics_port_in_use') from None


class WindowsProcesses:
    def __init__(self):
        require(sys.platform == 'win32', 'windows_required')
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'OpenProcess': ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
            'QueryFullProcessImageNameW': ([wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            'GetProcessTimes': ([wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4, wintypes.BOOL),
            'WaitForSingleObject': ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            'TerminateProcess': ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            'CreateMutexW': ([ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            'ReleaseMutex': ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes = args
            function.restype = result

    @contextmanager
    def lock(self):
        name = 'Global\\BoyanKB-Trial-' + hashlib.sha256(normalized_path(ROOT).encode()).hexdigest()
        handle = self.api.CreateMutexW(None, False, name)
        require(handle, 'process_lock_failed')
        acquired = False
        try:
            acquired = self.api.WaitForSingleObject(handle, 0) in {WAIT_OBJECT_0, WAIT_ABANDONED}
            require(acquired, 'runner_busy')
            yield
        finally:
            if acquired:
                self.api.ReleaseMutex(handle)
            self.api.CloseHandle(handle)

    @contextmanager
    def opened(self, pid, terminate=False):
        handle = self.api.OpenProcess(0x1000 | 0x100000 | (1 if terminate else 0), False, pid)
        if not handle:
            require(ctypes.get_last_error() == 87, 'process_query_denied')
            yield None
            return
        try:
            yield handle
        finally:
            self.api.CloseHandle(handle)

    def active(self, handle):
        result = self.api.WaitForSingleObject(handle, 0)
        require(result in {WAIT_OBJECT_0, WAIT_TIMEOUT}, 'process_query_failed')
        return result == WAIT_TIMEOUT

    def identity(self, handle):
        size = wintypes.DWORD(32768)
        path = ctypes.create_unicode_buffer(size.value)
        require(self.api.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)), 'process_image_query_failed')
        created, ended, kernel, user = (wintypes.FILETIME() for _ in range(4))
        require(self.api.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(ended), ctypes.byref(kernel), ctypes.byref(user)), 'process_time_query_failed')
        return {'executable': normalized_path(path.value), 'creation_time': (created.dwHighDateTime << 32) | created.dwLowDateTime}

    def terminate(self, handle):
        require(self.api.TerminateProcess(handle, 0), 'process_stop_failed')
        require(self.api.WaitForSingleObject(handle, 10000) == WAIT_OBJECT_0, 'process_stop_timeout')


def verify_identity(processes, handle, state):
    identity = processes.identity(handle)
    require(normalized_path(identity['executable']) == normalized_path(state['executable']) and identity['creation_time'] == state['creation_time'], 'process_identity_mismatch')


def process_status(processes, state):
    if not state:
        return {'status': 'NOT_STARTED', 'ready': False}
    with processes.opened(state['pid']) as handle:
        if handle is None or not processes.active(handle):
            return {'status': 'STOPPED', 'pid': state['pid'], 'ready': False}
        verify_identity(processes, handle, state)
        ready = metrics_ready()
        require(processes.active(handle), 'process_exited_during_status')
        return {'status': 'RUNNING' if ready else 'STARTING_OR_DISCONNECTED', 'pid': state['pid'], 'tunnel_id': state['tunnel_id'], 'ready': ready}


def start(processes):
    prepared = prepared_state()
    old = load_process()
    if old:
        require(old['tunnel_id'] == prepared['tunnel_id'], 'process_tunnel_mismatch')
        current = process_status(processes, old)
        if current['status'] != 'STOPPED':
            return current
    gateway_ready()
    require(EXECUTABLE.is_file(), 'cloudflared_executable_missing')
    metrics_port_free()
    token = tunnel_token()
    validate_token_scope(token, prepared)
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    if CONFIG.exists():
        require(CONFIG.is_file() and not CONFIG.is_symlink() and CONFIG.read_bytes() == b'{}\n', 'runner_config_changed')
    else:
        with CONFIG.open('xb') as handle:
            handle.write(b'{}\n')
    command = [str(EXECUTABLE), 'tunnel', '--config', str(CONFIG), '--no-autoupdate', '--metrics', METRICS, '--loglevel', 'error', '--management-diagnostics=false', 'run']
    try:
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=child_environment(token), cwd=str(DIRECTORY), creationflags=CREATE_NO_WINDOW, close_fds=True, shell=False)
    except OSError:
        raise RunnerError('cloudflared_start_failed') from None
    token = None
    handle = int(child._handle)
    try:
        require(processes.active(handle), 'cloudflared_exited')
        identity = processes.identity(handle)
        require(normalized_path(identity['executable']) == normalized_path(EXECUTABLE), 'launched_executable_mismatch')
        state = {'schema_version': 1, 'host': HOST, 'tunnel_id': prepared['tunnel_id'], 'pid': child.pid, **identity, 'metrics': METRICS}
        save_process(state)
    except Exception:
        if processes.active(handle):
            processes.terminate(handle)
        raise
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        require(processes.active(handle), 'cloudflared_exited')
        if metrics_ready():
            require(processes.active(handle), 'cloudflared_exited')
            return {'status': 'RUNNING', 'pid': child.pid, 'tunnel_id': prepared['tunnel_id'], 'ready': True}
        time.sleep(1)
    return {'status': 'STARTING_OR_DISCONNECTED', 'pid': child.pid, 'tunnel_id': prepared['tunnel_id'], 'ready': False}


def stop(processes):
    state = load_process()
    if not state:
        return {'status': 'NOT_STARTED', 'ready': False}
    with processes.opened(state['pid'], terminate=True) as handle:
        if handle is None or not processes.active(handle):
            return {'status': 'STOPPED', 'pid': state['pid'], 'ready': False}
        verify_identity(processes, handle, state)
        processes.terminate(handle)
    return {'status': 'STOPPED', 'pid': state['pid'], 'ready': False}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('start', 'status', 'stop'), default='status', nargs='?')
    args = parser.parse_args(argv)
    try:
        processes = WindowsProcesses()
        with processes.lock():
            if args.action == 'start':
                result = start(processes)
            elif args.action == 'stop':
                result = stop(processes)
            else:
                result = process_status(processes, load_process())
        print(json.dumps(result))
        return 0 if result['status'] != 'STARTING_OR_DISCONNECTED' else 2
    except RunnerError as error:
        print(json.dumps({'status': 'ERROR', 'code': str(error)}))
        return 1
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        print(json.dumps({'status': 'ERROR', 'code': 'runner_failed'}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
