import argparse
import base64
import errno
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time


ROOT = Path('/opt/rustdesk')
VERSION = '1.1.16'
TCP_PORTS = (21115, 21116, 21117)
WEBSOCKET_PORTS = (21118, 21119)


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def command(arguments, timeout=15):
    result = subprocess.run(arguments, capture_output=True, timeout=timeout)
    require(result.returncode == 0, 'VALIDATION_COMMAND_FAILED')
    return result.stdout


def connectable(port, timeout=0.5):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=timeout):
            return True
    except OSError:
        return False


def udp_bound(port):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.bind(('127.0.0.1', port))
        except OSError as error:
            return error.errno == errno.EADDRINUSE
    return False


def environment():
    path = ROOT / 'config/.env'
    require(not path.is_symlink() and path.resolve().is_relative_to(ROOT), 'UNSAFE_ENV_PATH')
    require(path.stat().st_uid == 0 and path.stat().st_mode & 0o777 == 0o600, 'ENV_PERMISSIONS_INVALID')
    values = dict(line.split('=', 1) for line in path.read_text().splitlines() if '=' in line)
    secret = base64.b64decode(values.get('KEY', ''), validate=True)
    require(len(secret) == 64, 'KEY_FORMAT_INVALID')
    public = base64.b64encode(secret[32:]).decode()
    require(values.get('RUSTDESK_PUBLIC_KEY') == public, 'KEY_PAIR_MISMATCH')
    result = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8', 'RUST_LOG': 'warn'}
    for key in ('KEY', 'TOTAL_BANDWIDTH', 'SINGLE_BANDWIDTH', 'LIMIT_SPEED'):
        result[key] = values[key]
    return result, public


def process_usage(process):
    fields = Path(f'/proc/{process.pid}/stat').read_text().rsplit(') ', 1)[1].split()
    cpu_seconds = (int(fields[11]) + int(fields[12])) / os.sysconf('SC_CLK_TCK')
    status = Path(f'/proc/{process.pid}/status').read_text()
    resident = re.search(r'^VmRSS:\s+(\d+)\s+kB$', status, re.MULTILINE)
    require(resident is not None, 'PROCESS_MEMORY_UNAVAILABLE')
    return {'cpu_seconds': cpu_seconds, 'rss_bytes': int(resident.group(1)) * 1024}


def interrupted(signum, frame):
    raise RuntimeError('VALIDATION_INTERRUPTED')


def stop(processes):
    for process in processes.values():
        if process.poll() is None:
            process.terminate()
    outputs = []
    for process in processes.values():
        try:
            output, errors = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            output, errors = process.communicate(timeout=5)
        outputs.append(output + errors)
    require(all(process.poll() is not None for process in processes.values()), 'VALIDATION_PROCESS_LEAK')
    return b''.join(outputs)


def isolated(parent_netns):
    require(parent_netns is not None, 'PARENT_NAMESPACE_REQUIRED')
    current_netns = os.stat('/proc/self/ns/net').st_ino
    require(current_netns != parent_netns, 'NETWORK_NAMESPACE_NOT_ISOLATED')
    require(current_netns != os.stat('/proc/1/ns/net').st_ino, 'HOST_NETWORK_NAMESPACE_REFUSED')
    command(['ip', 'link', 'set', 'lo', 'up'])
    validation = ROOT / 'validation'
    require(validation.is_dir() and not validation.is_symlink() and validation.resolve() == validation, 'UNSAFE_VALIDATION_PATH')
    workdir = Path(tempfile.mkdtemp(prefix='run-', dir=validation))
    env, public = environment()
    env['DB_URL'] = str(workdir / 'db_v2.sqlite3')
    versions = {}
    for name in ('hbbs', 'hbbr'):
        value = command([str(ROOT / 'current' / name), '--version']).decode().strip()
        require(value == name + ' ' + VERSION, 'BINARY_VERSION_MISMATCH')
        versions[name] = VERSION
    processes = {}
    result = {'network_namespace_isolated': True, 'versions': versions}
    try:
        for name, arguments in [('hbbs', ['-p', '21116', '-r', '127.0.0.1:21117']), ('hbbr', ['-p', '21117'])]:
            processes[name] = subprocess.Popen([str(ROOT / 'current' / name), *arguments], cwd=workdir, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            require(all(process.poll() is None for process in processes.values()), 'SERVER_EXITED_DURING_STARTUP')
            if all(connectable(port, 0.1) for port in TCP_PORTS + WEBSOCKET_PORTS) and udp_bound(21116):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError('LISTENERS_NOT_READY')
        result['servers_alive'] = True
        result['native_tcp_connects'] = {str(port): connectable(port) for port in TCP_PORTS}
        result['udp_21116_bound'] = udp_bound(21116)
        result['websocket_listeners_present'] = {str(port): connectable(port) for port in WEBSOCKET_PORTS}
        require(all(result['native_tcp_connects'].values()) and result['udp_21116_bound'] and all(result['websocket_listeners_present'].values()), 'LISTENER_CHECK_FAILED')
        firewall = ROOT / 'config/firewall.nft'
        require(not firewall.is_symlink() and firewall.resolve().is_relative_to(ROOT), 'UNSAFE_FIREWALL_PATH')
        command(['nft', '--check', '--file', str(firewall)])
        command(['nft', '--file', str(firewall)])
        result['websocket_blocked_by_firewall'] = {str(port): not connectable(port, 1) for port in WEBSOCKET_PORTS}
        result['native_tcp_after_firewall'] = {str(port): connectable(port) for port in TCP_PORTS}
        require(all(result['websocket_blocked_by_firewall'].values()), 'WEBSOCKET_FIREWALL_FAILED')
        require(all(result['native_tcp_after_firewall'].values()), 'NATIVE_TCP_FIREWALL_REGRESSION')
        before = {name: process_usage(process) for name, process in processes.items()}
        started = time.monotonic()
        time.sleep(2)
        elapsed = time.monotonic() - started
        after = {name: process_usage(process) for name, process in processes.items()}
        result['idle_resource_sample'] = {
            'duration_seconds': round(elapsed, 3),
            'rss_bytes_total': sum(value['rss_bytes'] for value in after.values()),
            'cpu_percent_one_core': round(sum(after[name]['cpu_seconds'] - before[name]['cpu_seconds'] for name in processes) / elapsed * 100, 3),
            'rss_bytes_per_process': {name: value['rss_bytes'] for name, value in after.items()},
        }
        require(all(process.poll() is None for process in processes.values()), 'SERVER_EXITED_DURING_VALIDATION')
    finally:
        output = stop(processes)
        require(env['KEY'].encode() not in output and public.encode() not in output, 'KEY_FOUND_IN_CAPTURED_LOGS')
    result['all_processes_stopped'] = True
    result['keys_absent_from_captured_logs'] = True
    result['passed'] = True
    (workdir / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def launch():
    parent_netns = os.stat('/proc/self/ns/net').st_ino
    process = subprocess.Popen(['unshare', '--net', sys.executable, str(Path(__file__).resolve()), '--isolated', '--parent-netns', str(parent_netns)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        output, errors = process.communicate(timeout=90)
        require(process.returncode == 0, 'ISOLATED_VALIDATION_FAILED')
        result = json.loads(output)
        require(result.get('passed') is True, 'ISOLATED_VALIDATION_NOT_PASSED')
        return result
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--isolated', action='store_true')
    parser.add_argument('--parent-netns', type=int)
    args = parser.parse_args()
    require(sys.platform == 'linux' and os.geteuid() == 0, 'LINUX_ROOT_REQUIRED')
    require(ROOT.resolve() == ROOT and ROOT.is_dir(), 'INSTALLATION_MISSING_OR_UNSAFE')
    os.umask(0o077)
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    result = isolated(args.parent_netns) if args.isolated else launch()
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as error:
        print(json.dumps({'passed': False, 'error': str(error)}), file=sys.stderr)
        raise SystemExit(1)
    except Exception:
        print(json.dumps({'passed': False, 'error': 'VALIDATION_FAILED'}), file=sys.stderr)
        raise SystemExit(1)
