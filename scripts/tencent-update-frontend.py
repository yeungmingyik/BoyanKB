import argparse
import copy
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import uuid


ROOT = Path('/opt/boyankb')
PROJECT = 'boyankb-tencent'
SERVICES = {'gateway', 'frontend', 'app', 'docreader', 'postgres', 'redis', 'ollama', 'document-renderer', 'neo4j'}
IMAGE_PATTERN = r'boyankb/frontend:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}@sha256:[a-f0-9]{64}'


def require(value, code):
    if not value:
        raise RuntimeError(code)


def command(arguments, environment=None, payload=None):
    result = subprocess.run(arguments, cwd=ROOT, env=environment, input=payload, capture_output=True, timeout=240)
    require(result.returncode == 0, 'COMMAND_FAILED')
    return result.stdout


def docker(arguments, environment=None, payload=None):
    return command(['docker', '--host', 'unix:///var/run/docker.sock', *arguments], environment, payload)


def split_image(reference):
    require(isinstance(reference, str) and re.fullmatch(IMAGE_PATTERN, reference), 'FIXED_FRONTEND_IMAGE_REQUIRED')
    return reference.split('@', 1)


def verify_image(reference):
    tag, digest = split_image(reference)
    images = json.loads(docker(['image', 'inspect', tag, reference]))
    require(len(images) == 2, 'EXACT_IMAGE_REFERENCES_REQUIRED')
    require(all(item.get('Id') == digest and item.get('Os') == 'linux' and item.get('Architecture') == 'amd64' for item in images), 'IMAGE_ID_OR_PLATFORM_MISMATCH')


def validate_lock(configuration, lock):
    require(set(configuration.get('services', {})) == SERVICES, 'SERVICE_SET_MISMATCH')
    require(lock.get('platform') == 'linux/amd64', 'LOCK_PLATFORM_MISMATCH')
    entries = lock.get('images', [])
    require(len(entries) == 9, 'NINE_LOCKED_IMAGES_REQUIRED')
    references = [entry['tag'] + '@' + entry['id'] for entry in entries]
    require(len(set(references)) == 9 and set(references) == {item['image'] for item in configuration['services'].values()}, 'IMAGE_LOCK_MISMATCH')


def updated_files(original, lock, previous, requested):
    split_image(previous)
    tag, digest = split_image(requested)
    text = original.decode('utf-8')
    sections = list(re.finditer(r'(?ms)^  frontend:\r?\n(?P<body>.*?)(?=^  [a-zA-Z0-9_-]+:|^\S|\Z)', text))
    require(len(sections) == 1, 'EXACT_FRONTEND_SECTION_REQUIRED')
    section = sections[0]
    body, count = re.subn(r'(?m)^    image: ' + re.escape(previous) + r'(?=\r?$)', '    image: ' + requested, section.group('body'))
    require(count == 1, 'FRONTEND_IMAGE_LINE_MISMATCH')
    updated = text[:section.start('body')] + body + text[section.end('body'):]
    candidate = copy.deepcopy(lock)
    entries = [entry for entry in candidate['images'] if entry['tag'] + '@' + entry['id'] == previous]
    require(len(entries) == 1, 'EXACT_FRONTEND_LOCK_REQUIRED')
    entries[0].update(tag=tag, id=digest, digests=['boyankb/frontend@' + digest])
    return updated.encode('utf-8'), (json.dumps(candidate, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def validate_change(before, after, requested):
    split_image(requested)
    expected = copy.deepcopy(before)
    expected['services']['frontend']['image'] = requested
    require(expected == after, 'UNEXPECTED_CONFIGURATION_CHANGE')


def compose(environment, payload=None):
    files = ['-f', '-' if payload is not None else str(ROOT / 'docker-compose.tencent.yml'), '-f', str(ROOT / 'docker-compose.tencent-ip-https.yml')]
    return ['compose', '--project-name', PROJECT, '--project-directory', str(ROOT), '--env-file', str(ROOT / '.env.tencent'), *files]


def configuration(environment, payload=None):
    return json.loads(docker(compose(environment, payload) + ['config', '--format', 'json'], environment, payload))


def containers():
    identifiers = docker(['ps', '-aq', '--filter', 'label=com.docker.compose.project=' + PROJECT]).decode().split()
    require(len(identifiers) == 9, 'NINE_SERVICE_CONTAINERS_REQUIRED')
    items = json.loads(docker(['inspect', *identifiers]))
    result = {item['Config']['Labels']['com.docker.compose.service']: item for item in items}
    require(len(result) == 9 and set(result) == SERVICES, 'SERVICE_SET_MISMATCH')
    require(all(item['State'].get('Health', {}).get('Status') == 'healthy' for item in result.values()), 'SERVICES_NOT_HEALTHY')
    require(all(not item['HostConfig'].get('PortBindings') for name, item in result.items() if name != 'gateway'), 'INTERNAL_PORT_PUBLISHED')
    return result


def validate_containers(before, after, reference):
    require(set(before) == set(after) == SERVICES, 'SERVICE_SET_CHANGED')
    require(all(before[name]['Id'] == item['Id'] for name, item in after.items() if name != 'frontend'), 'OTHER_SERVICE_REPLACED')
    require(after['frontend']['Config']['Image'] == reference, 'FRONTEND_CONTAINER_IMAGE_MISMATCH')
    require(all(before[name]['HostConfig'].get('PortBindings') == item['HostConfig'].get('PortBindings') for name, item in after.items()), 'PUBLISHED_PORTS_CHANGED')


def http_checks():
    results = {}
    cases = [('GET', '/login', 200), ('GET', '/api/v1/knowledge-bases', 401), ('GET', '/api/v1/skills/catalog', 401), ('POST', '/api/v1/auth/register', 403), ('GET', '/api/v1/system/admin', 404)]
    for method, path, expected in cases:
        arguments = ['curl', '--noproxy', '*', '--silent', '--show-error', '--max-time', '20', '--output', '/dev/null', '--write-out', '%{http_code}', '--resolve', '193.112.17.218:443:127.0.0.1', '--request', method]
        if method == 'POST':
            arguments += ['--header', 'Content-Type: application/json', '--data', '{}']
        actual = command(arguments + ['https://193.112.17.218' + path]).decode()
        require(actual == str(expected), 'HTTPS_BOUNDARY_CHECK_FAILED')
        results[method + ' ' + path] = expected
    return results


def checked_file(path):
    require(path.is_absolute() and path.resolve() == path and path.is_relative_to(ROOT), 'UNSAFE_FILE_PATH')
    mode = path.lstat()
    require(stat.S_ISREG(mode.st_mode) and mode.st_uid == 0 and mode.st_nlink == 1 and not mode.st_mode & 0o022, 'ROOT_REGULAR_FILE_REQUIRED')
    return path.read_bytes()


def exclusive_write(path, payload):
    require(path.parent.resolve() == path.parent and path.parent.is_relative_to(ROOT), 'UNSAFE_OUTPUT_PATH')
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def replace_file(path, expected, payload):
    require(checked_file(path) == expected, 'CONFIGURATION_FILE_CHANGED')
    temporary = path.with_name(path.name + '.frontend-' + uuid.uuid4().hex)
    exclusive_write(temporary, payload)
    os.replace(temporary, path)


def recreate_frontend(environment):
    docker(compose(environment) + ['up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--force-recreate', '--wait', '--wait-timeout', '180', 'frontend'], environment)


def error_code(error):
    return str(error) if isinstance(error, RuntimeError) and re.fullmatch(r'[A-Z0-9_]+', str(error)) else type(error).__name__


def deploy(accounts, reference, previous, original, proposed, environment, before, original_configuration, env_digest, report_path, report):
    changed = []
    try:
        for path, payload in proposed.items():
            replace_file(path, original[path], payload)
            changed.append(path)
        validate_change(original_configuration, configuration(environment), reference)
        recreate_frontend(environment)
        after = containers()
        validate_containers(before, after, reference)
        require(before['frontend']['Id'] != after['frontend']['Id'], 'FRONTEND_NOT_RECREATED')
        checks = http_checks()
        require(hashlib.sha256(checked_file(ROOT / '.env.tencent')).hexdigest() == env_digest, 'ENVIRONMENT_FILE_CHANGED')
        report.update(status='VERIFIED', healthy_services=9, only_frontend_recreated=True, environment_unchanged=True, https_checks=checks)
    except Exception as error:
        report.update(status='FAILED', error=error_code(error))
        try:
            for path in reversed(changed):
                replace_file(path, proposed[path], original[path])
            require(configuration(environment) == original_configuration, 'ROLLBACK_CONFIGURATION_MISMATCH')
            if changed:
                recreate_frontend(environment)
            validate_containers(before, containers(), previous)
            report.update(status='ROLLED_BACK', rollback_https_checks=http_checks())
        except Exception as rollback_error:
            report.update(status='ROLLBACK_REQUIRED', rollback_error=error_code(rollback_error))
        accounts.save_private(report_path, report, replace=True)
        print(json.dumps({**report, 'report': str(report_path)}), flush=True)
        raise RuntimeError('FRONTEND_UPDATE_' + report['status']) from None
    accounts.save_private(report_path, report, replace=True)
    print(json.dumps({**report, 'report': str(report_path)}), flush=True)


def apply(accounts, reference, previous, original, proposed, environment, before, original_configuration, env_digest):
    import fcntl

    descriptor = os.open(ROOT / '.frontend-update.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        require(os.fstat(descriptor).st_uid == 0 and stat.S_IMODE(os.fstat(descriptor).st_mode) == 0o600 and os.fstat(descriptor).st_nlink == 1, 'PRIVATE_RELEASE_LOCK_REQUIRED')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(all(checked_file(path) == payload for path, payload in original.items()), 'CONFIGURATION_FILE_CHANGED')
        require(hashlib.sha256(checked_file(ROOT / '.env.tencent')).hexdigest() == env_digest, 'ENVIRONMENT_FILE_CHANGED')
        require(configuration(environment) == original_configuration, 'CONFIGURATION_CHANGED_SINCE_PLAN')
        require({name: item['Id'] for name, item in containers().items()} == {name: item['Id'] for name, item in before.items()}, 'CONTAINERS_CHANGED_SINCE_PLAN')
        backup_root = ROOT / 'backups'
        require(backup_root.resolve() == backup_root and backup_root.is_dir() and backup_root.stat().st_uid == 0 and not backup_root.stat().st_mode & 0o077, 'PRIVATE_BACKUP_DIRECTORY_REQUIRED')
        backup = backup_root / ('frontend-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
        backup.mkdir(mode=0o700)
        for path, payload in original.items():
            exclusive_write(backup / path.name, payload)
        report_path = backup / 'report.json'
        report = {'status': 'PREPARED', 'previous_image': previous, 'requested_image': reference, 'backup_directory': str(backup), 'backup_sha256': {path.name: hashlib.sha256(payload).hexdigest() for path, payload in original.items()}}
        accounts.save_private(report_path, report)
        deploy(accounts, reference, previous, original, proposed, environment, before, original_configuration, env_digest, report_path, report)
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    split_image(args.image)
    spec = importlib.util.spec_from_file_location('frontend_update_accounts', ROOT / 'scripts/tencent-accounts.py')
    accounts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(accounts)
    accounts.host_guard()
    env = accounts.private_path(ROOT / '.env.tencent').read_bytes()
    variables = set(re.findall(r'^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=', env.decode(), re.M))
    environment = {key: value for key, value in os.environ.items() if key not in variables and not key.startswith(('DOCKER_', 'COMPOSE_'))}
    compose_path = ROOT / 'docker-compose.tencent.yml'
    lock_path = ROOT / 'deployment/tencent/images.lock.json'
    original = {path: checked_file(path) for path in (compose_path, lock_path)}
    current = configuration(environment)
    lock = json.loads(original[lock_path])
    validate_lock(current, lock)
    previous = current['services']['frontend']['image']
    verify_image(previous)
    verify_image(args.image)
    before = containers()
    require(before['frontend']['Config']['Image'] == previous, 'RUNNING_FRONTEND_LOCK_MISMATCH')
    candidate_compose, candidate_lock = updated_files(original[compose_path], lock, previous, args.image)
    candidate = configuration(environment, candidate_compose)
    validate_change(current, candidate, args.image)
    validate_lock(candidate, json.loads(candidate_lock))
    checks = http_checks()
    print(json.dumps({'mode': 'APPLY' if args.apply else 'PLAN', 'previous_image': previous, 'requested_image': args.image, 'configuration_change': 'services.frontend.image', 'healthy_services': 9, 'https_checks': checks, 'no_change': args.image == previous}), flush=True)
    if args.apply and args.image != previous:
        apply(accounts, args.image, previous, original, {compose_path: candidate_compose, lock_path: candidate_lock}, environment, before, current, hashlib.sha256(env).hexdigest())


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'FAILED', 'code': error_code(error)}), flush=True)
        raise SystemExit(1)
