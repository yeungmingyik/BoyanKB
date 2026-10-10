import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import pwd
import re
import subprocess
import sys
import zipfile


ROOT = Path('/opt/rustdesk')
VERSION = '1.1.16'
ARCHIVE_SHA256 = '0565c41affe6c3f409b0bddfaf5a24ccbf3f64f5f8e3fec250b69a0d5f6bdbcf'
BINARIES = {
    'hbbs': '374c821722b04f6d7ef063c78bc5b4db7109b8b9613508822d803e31fdc5b625',
    'hbbr': '3ad04953242db5a9a78d45d41d13b9c6ecff651881e3a7f594f8b36a321e2457',
    'rustdesk-utils': 'bdac47af7201b702e43a8262c08cef3e5703e081ddbf6c679c4ceef98d8744c9',
}
ASSETS = {
    'rustdesk.slice': 'systemd/rustdesk.slice',
    'rustdesk-hbbs.service': 'systemd/rustdesk-hbbs.service',
    'rustdesk-hbbr.service': 'systemd/rustdesk-hbbr.service',
    'rustdesk-firewall.service': 'systemd/rustdesk-firewall.service',
    'journald.conf': 'config/journald.conf',
    'firewall.nft': 'config/firewall.nft',
    'manage.py': 'manage.py',
    'validate.py': 'validate.py',
    'README.md': 'README.md',
    'LICENSE': 'LICENSE',
}
SERVICES = ['rustdesk-hbbs.service', 'rustdesk-hbbr.service']


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def command(args, timeout=30, check=True, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, **kwargs)
    require(not check or result.returncode == 0, 'COMMAND_FAILED:' + args[0])
    return result


def digest(data):
    return hashlib.sha256(data).hexdigest()


def platform_check():
    require(os.geteuid() == 0, 'ROOT_REQUIRED')
    require(platform.machine() == 'x86_64', 'ARCHITECTURE_MISMATCH')
    os_release = Path('/etc/os-release').read_text()
    require('ID=ubuntu' in os_release and 'VERSION_ID="24.04"' in os_release, 'OS_MISMATCH')
    require(Path('/opt').resolve() == Path('/opt') and not ROOT.is_symlink(), 'UNSAFE_ROOT')


def write_new(relative, content, mode):
    target = ROOT / relative
    require(target.parent.resolve().is_relative_to(ROOT), 'UNSAFE_DESTINATION')
    with target.open('xb') as handle:
        os.fchmod(handle.fileno(), mode)
        handle.write(content)


def stage():
    require(not os.path.lexists(ROOT), 'TARGET_ALREADY_EXISTS')
    payload = json.load(sys.stdin)
    archive_data = base64.b64decode(payload['archive'], validate=True)
    require(digest(archive_data) == ARCHIVE_SHA256, 'ARCHIVE_HASH_MISMATCH')
    require(set(payload['files']) == set(ASSETS), 'ASSET_SET_MISMATCH')
    files = {name: base64.b64decode(value, validate=True) for name, value in payload['files'].items()}
    with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
        require(set(archive.namelist()) == {'amd64/' + name for name in BINARIES}, 'ARCHIVE_CONTENT_MISMATCH')
        binaries = {name: archive.read('amd64/' + name) for name in BINARIES}
    require(all(digest(data) == BINARIES[name] for name, data in binaries.items()), 'BINARY_HASH_MISMATCH')
    ROOT.mkdir(mode=0o755)
    os.chmod(ROOT, 0o755)
    for directory, mode in [('releases', 0o755), ('releases/' + VERSION, 0o755), ('config', 0o700), ('data', 0o700), ('systemd', 0o755), ('validation', 0o700)]:
        (ROOT / directory).mkdir(mode=mode)
        os.chmod(ROOT / directory, mode)
    for name, data in binaries.items():
        write_new('releases/' + VERSION + '/' + name, data, 0o755)
    for name, data in files.items():
        write_new(ASSETS[name], data, 0o644)
    (ROOT / 'current').symlink_to(ROOT / 'releases' / VERSION, target_is_directory=True)
    keypair = command([str(ROOT / 'current/rustdesk-utils'), 'genkeypair']).stdout
    values = dict(line.split(':', 1) for line in keypair.splitlines() if ':' in line)
    secret = values.get('Secret Key', '').strip()
    public = values.get('Public Key', '').strip()
    secret_bytes = base64.b64decode(secret, validate=True)
    public_bytes = base64.b64decode(public, validate=True)
    require(len(secret_bytes) == 64 and len(public_bytes) == 32 and secret_bytes[32:] == public_bytes, 'KEY_GENERATION_FAILED')
    environment = {'KEY': secret, 'RUSTDESK_PUBLIC_KEY': public, 'RUSTDESK_RELAY': '193.112.17.218:21117', 'RUST_LOG': 'warn', 'TOTAL_BANDWIDTH': '0.9', 'SINGLE_BANDWIDTH': '0.9', 'LIMIT_SPEED': '0.9'}
    write_new('config/.env', ''.join(key + '=' + value + '\n' for key, value in environment.items()).encode(), 0o600)
    manifest = {'version': VERSION, 'archive_sha256': ARCHIVE_SHA256, 'binaries': BINARIES, 'assets': {ASSETS[name]: digest(data) for name, data in files.items()}}
    write_new('manifest.json', (json.dumps(manifest, indent=2) + '\n').encode(), 0o644)
    return {'staged': True, 'root': str(ROOT), 'version': VERSION, 'services_started': False, 'firewall_changed': False}


def integrity():
    require(ROOT.is_dir() and ROOT.resolve() == ROOT, 'TARGET_MISSING_OR_UNSAFE')
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    require(manifest['version'] == VERSION and manifest['archive_sha256'] == ARCHIVE_SHA256, 'MANIFEST_MISMATCH')
    require(manifest['binaries'] == BINARIES and set(manifest['assets']) == set(ASSETS.values()), 'MANIFEST_CONTENT_MISMATCH')
    for name, expected in BINARIES.items():
        path = ROOT / 'releases' / VERSION / name
        require(not path.is_symlink() and path.is_file() and digest(path.read_bytes()) == expected, 'BINARY_CHANGED')
    for relative, expected in manifest['assets'].items():
        path = ROOT / relative
        require(not path.is_symlink() and path.is_file() and digest(path.read_bytes()) == expected, 'ASSET_CHANGED')
    require((ROOT / 'current').resolve() == ROOT / 'releases' / VERSION, 'CURRENT_RELEASE_CHANGED')
    environment = ROOT / 'config/.env'
    require(not environment.is_symlink() and environment.stat().st_uid == 0 and environment.stat().st_mode & 0o777 == 0o600, 'ENV_PERMISSIONS_INVALID')
    return manifest


def check():
    integrity()
    units = sorted(str(path) for path in (ROOT / 'systemd').iterdir())
    units_check = command(['systemd-analyze', 'verify', *units], check=False)
    require(units_check.returncode == 0, 'SYSTEMD_UNITS_INVALID')
    command(['nft', '--check', '--file', str(ROOT / 'config/firewall.nft')])
    versions = {name: command([str(ROOT / 'current' / name), '--version']).stdout.strip() for name in SERVICES_BINARY_NAMES}
    require(all(VERSION in value for value in versions.values()), 'VERSION_MISMATCH')
    return {'integrity': True, 'systemd_units_valid': True, 'firewall_syntax_valid': True, 'versions': versions}


SERVICES_BINARY_NAMES = ('hbbs', 'hbbr')


def activate():
    check()
    require(command(['nft', 'list', 'table', 'inet', 'rustdesk'], check=False).returncode != 0, 'FIREWALL_TABLE_ALREADY_EXISTS')
    listeners = command(['ss', '-lntuH']).stdout
    require(not re.search(r':2111[5-9]\s', listeners), 'PORTS_IN_USE')
    destinations = {Path('/etc/systemd/system') / name: ROOT / 'systemd' / name for name in ['rustdesk.slice', 'rustdesk-firewall.service', *SERVICES]}
    destinations[Path('/etc/systemd/journald@rustdesk.conf')] = ROOT / 'config/journald.conf'
    require(all(not os.path.lexists(path) for path in destinations), 'SYSTEMD_TARGET_ALREADY_EXISTS')
    try:
        pwd.getpwnam('rustdesk')
    except KeyError:
        pass
    else:
        raise RuntimeError('SERVICE_USER_ALREADY_EXISTS')
    command(['useradd', '--system', '--user-group', '--no-create-home', '--home-dir', str(ROOT / 'data'), '--shell', '/usr/sbin/nologin', 'rustdesk'])
    account = pwd.getpwnam('rustdesk')
    os.chown(ROOT / 'data', account.pw_uid, account.pw_gid)
    for destination, source in destinations.items():
        destination.symlink_to(source)
    command(['systemctl', 'daemon-reload'])
    try:
        command(['systemctl', 'enable', '--now', *SERVICES], timeout=60)
        result = status()
        require(all(service['active'] for service in result['services'].values()), 'SERVICE_NOT_ACTIVE')
    except Exception:
        for operation in [['systemctl', 'stop', *SERVICES], ['systemctl', 'disable', *SERVICES], ['systemctl', 'stop', 'rustdesk-firewall.service']]:
            try:
                command(operation, check=False, timeout=60)
            except Exception:
                pass
        raise RuntimeError('ACTIVATION_FAILED_REVIEW_RETAINED_RUSTDESK_RESOURCES')
    return result


def status():
    integrity()
    properties = ['ActiveState', 'SubState', 'MainPID', 'NRestarts', 'MemoryCurrent', 'TasksCurrent', 'CPUUsageNSec', 'User', 'Slice', 'ProtectSystem', 'ProtectHome', 'NoNewPrivileges', 'ReadWritePaths', 'InaccessiblePaths', 'LogNamespace']
    services = {}
    for name in SERVICES:
        result = command(['systemctl', 'show', name, '--property=' + ','.join(properties)])
        values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        services[name] = {'active': values.get('ActiveState') == 'active', 'enabled': command(['systemctl', 'is-enabled', name], check=False).stdout.strip(), 'properties': values}
    resource = command(['systemctl', 'show', 'rustdesk.slice', '--property=CPUQuotaPerSecUSec,MemoryHigh,MemoryMax,MemorySwapMax,TasksMax,CPUWeight,IOWeight,MemoryCurrent']).stdout
    firewall = command(['nft', 'list', 'table', 'inet', 'rustdesk'], check=False)
    return {'services': services, 'resource_limits': dict(line.split('=', 1) for line in resource.splitlines() if '=' in line), 'firewall_active': firewall.returncode == 0, 'listeners': command(['ss', '-lntuH']).stdout.splitlines()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['stage', 'check', 'activate', 'status'])
    args = parser.parse_args()
    platform_check()
    result = {'stage': stage, 'check': check, 'activate': activate, 'status': status}[args.action]()
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
    except Exception:
        print('RUSTDESK_OPERATION_FAILED', file=sys.stderr)
        raise SystemExit(1)
