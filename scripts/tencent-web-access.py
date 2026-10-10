import argparse
import datetime
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import uuid


ROOT = Path('/opt/boyankb')
PROJECT = 'boyankb-tencent'


def require(value, code):
    if not value:
        raise RuntimeError(code)


def command(arguments, environment=None):
    result = subprocess.run(arguments, cwd=ROOT, env=environment, capture_output=True, timeout=240)
    require(result.returncode == 0, 'COMMAND_FAILED')
    return result.stdout


def containers():
    identities = command(['docker', '--host', 'unix:///var/run/docker.sock', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + PROJECT]).decode().split()
    require(len(identities) == 9, 'NINE_SERVICE_CONTAINERS_REQUIRED')
    items = json.loads(command(['docker', '--host', 'unix:///var/run/docker.sock', 'inspect', *identities]))
    return {item['Config']['Labels']['com.docker.compose.service']: item for item in items}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--allow', nargs='+', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    networks = [str(ipaddress.ip_network(value)) for value in args.allow]
    require(len(networks) == len(set(networks)) and len(networks) <= 32, 'INVALID_NETWORK_LIST')
    value = ' '.join(networks)
    spec = importlib.util.spec_from_file_location('accounts', ROOT / 'scripts/tencent-accounts.py')
    accounts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(accounts)
    accounts.host_guard()
    target = accounts.private_path(ROOT / '.env.tencent')
    require(target.stat().st_nlink == 1, 'ENV_HARDLINK_REJECTED')
    original = target.read_bytes()
    lines = original.decode('utf-8').splitlines(keepends=True)
    matches = [index for index, line in enumerate(lines) if re.match(r'^\s*(?:export\s+)?WEB_ALLOWED_CIDRS\s*=', line)]
    require(len(matches) <= 1, 'DUPLICATE_ACCESS_SETTING')
    replacement = "WEB_ALLOWED_CIDRS='" + value + "'\n"
    if matches:
        lines[matches[0]] = replacement
    else:
        require(not original or original.endswith(b'\n'), 'ENV_FINAL_NEWLINE_REQUIRED')
        lines.append(replacement)
    updated = ''.join(lines).encode()
    before = containers()
    require(all(item['State'].get('Health', {}).get('Status') == 'healthy' for item in before.values()), 'SERVICES_NOT_HEALTHY')
    require(all(not item['HostConfig'].get('PortBindings') for name, item in before.items() if name != 'gateway'), 'INTERNAL_PORT_PUBLISHED')
    variables = set(re.findall(r'^([A-Z][A-Z0-9_]*)=', updated.decode(), re.M))
    environment = {key: item for key, item in os.environ.items() if key not in variables and not key.startswith(('DOCKER_', 'COMPOSE_'))}
    compose = ['docker', '--host', 'unix:///var/run/docker.sock', 'compose', '--project-name', PROJECT, '--env-file', '.env.tencent', '-f', 'docker-compose.tencent.yml', '-f', 'docker-compose.tencent-ip-https.yml']
    current = json.loads(command(compose + ['config', '--format', 'json'], environment))
    print(json.dumps({'mode': 'APPLY' if args.apply else 'PLAN', 'previous_allow': current['services']['gateway']['environment']['WEB_ALLOWED_CIDRS'], 'requested_allow': value}), flush=True)
    if not args.apply:
        return
    backup = ROOT / 'backups' / ('web-access-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8] + '.env')
    with os.fdopen(os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
        stream.write(original)
    temporary = target.with_name(target.name + '.web-access-' + uuid.uuid4().hex)
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
        stream.write(updated)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    configuration = json.loads(command(compose + ['config', '--format', 'json'], environment))
    current['services']['gateway']['environment']['WEB_ALLOWED_CIDRS'] = value
    require(configuration == current, 'UNEXPECTED_CONFIGURATION_CHANGE')
    command(compose + ['up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--force-recreate', '--wait', '--wait-timeout', '180', 'gateway'], environment)
    after = containers()
    require(set(after) == set(before), 'SERVICE_SET_CHANGED')
    require(all(item['State'].get('Health', {}).get('Status') == 'healthy' for item in after.values()), 'SERVICES_NOT_HEALTHY')
    require(all(before[name]['Id'] == item['Id'] for name, item in after.items() if name != 'gateway'), 'BUSINESS_CONTAINER_REPLACED')
    require(before['gateway']['HostConfig']['PortBindings'] == after['gateway']['HostConfig']['PortBindings'], 'GATEWAY_PORTS_CHANGED')
    report = {'allowed_cidrs': networks, 'healthy_services': 9, 'only_gateway_recreated': True, 'ports_unchanged': True, 'backup': str(backup)}
    accounts.save_private(ROOT / 'web-access-report.json', report, replace=True)
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'FAILED', 'code': str(error) if isinstance(error, RuntimeError) else type(error).__name__}), flush=True)
        raise SystemExit(1)
