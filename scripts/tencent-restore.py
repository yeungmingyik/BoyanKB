import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import uuid


ROOT = Path('/opt/boyankb')
SOURCE = ROOT / 'incoming/snapshot'
PROJECT = 'boyankb-tencent'
PUBLIC_IP = '193.112.17.218'
SERVICES = {'gateway', 'frontend', 'app', 'docreader', 'postgres', 'redis', 'ollama', 'document-renderer', 'neo4j'}
VOLUMES = {'postgres-data', 'redis-data', 'data-files', 'docreader-tmp', 'ollama-data', 'neo4j-data', 'neo4j-logs'}
ARCHIVES = {'data-files.tar.gz': ('data-files', '.'), 'redis-data.tar.gz': ('redis-data', '.'), 'neo4j-data.tar.gz': ('neo4j-data', '.'), 'ollama-models.tar.gz': ('ollama-data', 'models')}
COMPONENTS = set(ARCHIVES) | {'database.dump', 'runtime.env', 'source-counts.json', 'config.yaml', 'builtin_models.local.yaml'}
KB = 'd9285ed1-0760-474b-ba82-a13347598176'
ENV_KEYS = {'COMPOSE_PROJECT_NAME', 'PUBLIC_HOST', 'PUBLIC_ORIGIN', 'DB_USER', 'DB_NAME', 'DB_PASSWORD', 'REDIS_PASSWORD', 'GRPC_AUTH_TOKEN', 'SYSTEM_AES_KEY', 'SYSTEM_SIGNING_KEY', 'JWT_SECRET', 'BOYAN_NEO4J_PASSWORD', 'BOYAN_DOCUMENT_RENDERER_TOKEN', 'DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL', 'DEEPSEEK_MODEL_NAME', 'EMBEDDING_MODEL_NAME', 'WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL'}
BINDS = {
    'gateway': {'/etc/caddy/Caddyfile': 'deployment/tencent/Caddyfile'},
    'frontend': {'/etc/boyankb/frontend-entrypoint.sh': 'deployment/tencent/frontend-entrypoint.sh'},
    'app': {'/app/config/config.yaml': 'config/config.yaml', '/app/config/builtin_models.local.yaml': 'config/builtin_models.local.yaml'},
    'redis': {'/etc/boyankb/redis-entrypoint.sh': 'deployment/tencent/redis-entrypoint.sh'},
}
MOUNTS = {'postgres': {'/var/lib/postgresql/data': 'postgres-data'}, 'redis': {'/data': 'redis-data'}, 'app': {'/data/files': 'data-files', '/tmp/docreader': 'docreader-tmp'}, 'docreader': {'/tmp/docreader': 'docreader-tmp'}, 'ollama': {'/root/.ollama': 'ollama-data'}, 'neo4j': {'/data': 'neo4j-data', '/logs': 'neo4j-logs'}}
SERVICE_FILE_GROUPS = {'config/config.yaml': 1000, 'config/builtin_models.local.yaml': 1000, 'deployment/tencent/Caddyfile': 65532, 'deployment/tencent/Caddyfile.ip-https': 65532}


class RestoreError(Exception):
    pass


def require(condition, code):
    if not condition:
        raise RestoreError(code)


def emit(event, **values):
    print(json.dumps({'event': event, **values}), flush=True)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_digest(path):
    checksum = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(block)
            size += len(block)
    return {'size': size, 'sha256': checksum.hexdigest()}


def safe_path(path, private=False, required=True):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path and path.is_relative_to(ROOT), 'DEPLOYMENT_PATH_REQUIRED')
    for parent in (path, *path.parents):
        require(not parent.is_symlink(), 'SYMLINK_PATH_REJECTED')
        if parent == ROOT:
            break
    if required:
        details = path.stat()
        require(stat.S_ISREG(details.st_mode) and details.st_uid == 0, 'ROOT_REGULAR_FILE_REQUIRED')
        if private:
            require(not details.st_mode & 0o077, 'PRIVATE_FILE_REQUIRED')
    return path


def host_guard():
    require(sys.platform == 'linux' and os.geteuid() == 0 and platform.machine() == 'x86_64', 'UBUNTU_ROOT_AMD64_REQUIRED')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    require(release.get('ID', '').strip('"') == 'ubuntu' and release.get('VERSION_ID', '').strip('"') == '24.04', 'UBUNTU_24_04_REQUIRED')
    require(ROOT.is_dir() and ROOT.resolve() == ROOT and ROOT.stat().st_uid == 0, 'DEPLOYMENT_DIRECTORY_REQUIRED')
    marker = json.loads(safe_path(ROOT / '.deployment-target.json', True).read_bytes())
    require(marker == {'schema_version': 1, 'project': PROJECT, 'public_ip': PUBLIC_IP}, 'TARGET_MARKER_MISMATCH')
    require(not any(os.environ.get(key) for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH')), 'DOCKER_ENVIRONMENT_OVERRIDE_REJECTED')


def command(args, input_data=None, timeout=180, stdin=None, env=None):
    if args[0] == 'docker' and (len(args) < 2 or args[1] != '--host'):
        args = ['docker', '--host', 'unix:///var/run/docker.sock', *args[1:]]
    try:
        result = subprocess.run(args, input=input_data, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False, cwd=ROOT, env=env)
    except (OSError, subprocess.TimeoutExpired):
        raise RestoreError('COMMAND_FAILED') from None
    require(result.returncode == 0, 'COMMAND_FAILED')
    return result.stdout


def fresh_target():
    for kind, options in (('ps', ['-aq']), ('volume', ['ls', '-q']), ('network', ['ls', '-q'])):
        require(not command(['docker', kind, *options, '--filter', 'label=com.docker.compose.project=' + PROJECT]).strip(), 'TARGET_PROJECT_ALREADY_EXISTS')
    for kind, options, template in (('ps', ['-a'], '{{.Names}}'), ('volume', ['ls'], '{{.Name}}'), ('network', ['ls'], '{{.Name}}')):
        names = command(['docker', kind, *options, '--format', template]).decode().splitlines()
        require(not any(name == PROJECT or name.startswith(PROJECT + '_') or name.startswith(PROJECT + '-') for name in names), 'TARGET_NAMED_RESOURCE_ALREADY_EXISTS')


def parse_environment(raw):
    require(len(raw) <= 1024 * 1024, 'ENVIRONMENT_TOO_LARGE')
    result = {}
    for line in raw.decode('utf-8-sig').splitlines():
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)='((?:\\'|[^'])*)'", line)
        require(match is not None, 'SNAPSHOT_ENVIRONMENT_FORMAT_INVALID')
        key, value = match.groups()
        require(key in ENV_KEYS and key not in result, 'ENVIRONMENT_KEY_REJECTED')
        result[key] = value.replace("\\'", "'")
        require(result[key] and not any(char in result[key] for char in ('\x00', '\r', '\n')), 'ENVIRONMENT_VALUE_INVALID')
    require(set(result) == ENV_KEYS, 'ENVIRONMENT_KEYS_MISSING')
    require(result['COMPOSE_PROJECT_NAME'] == PROJECT and result['PUBLIC_HOST'] == PUBLIC_IP and result['PUBLIC_ORIGIN'] == 'http://' + PUBLIC_IP, 'ENVIRONMENT_TARGET_MISMATCH')
    require(len(result['SYSTEM_AES_KEY'].encode()) == 32, 'AES_KEY_LENGTH_INVALID')
    require(all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,62}', result[key]) for key in ('DB_USER', 'DB_NAME')), 'DATABASE_IDENTITY_INVALID')
    return result


def inventory(stream):
    result = {}
    total = 0
    with tarfile.open(fileobj=stream, mode='r|') as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            name = str(path)
            require(member.name and not path.is_absolute() and '..' not in path.parts and not any(char in member.name for char in ('\\', ':', '\x00', '\r', '\n')), 'ARCHIVE_PATH_REJECTED')
            require(len(name) <= 4096 and name not in result and len(result) < 200000, 'ARCHIVE_PATH_DUPLICATE_OR_LIMIT')
            require(member.isfile() or member.isdir(), 'ARCHIVE_LINK_OR_SPECIAL_FILE_REJECTED')
            require(name != '.' or member.isdir(), 'ARCHIVE_ROOT_NOT_DIRECTORY')
            require(0 <= member.uid <= 65535 and 0 <= member.gid <= 65535, 'ARCHIVE_OWNERSHIP_REJECTED')
            require(0 <= member.mode <= 0o7777 and not member.mode & (0o4000 if member.isdir() else 0o7000), 'ARCHIVE_PERMISSION_REJECTED')
            require(not getattr(member, 'sparse', None), 'SPARSE_ARCHIVE_REJECTED')
            for parent in path.parents:
                require(str(parent) not in result or result[str(parent)]['type'] == 'directory', 'ARCHIVE_FILE_AS_PARENT')
            common = {'type': 'directory' if member.isdir() else 'file', 'mode': member.mode & (0o7777 if member.isdir() else 0o777), 'uid': member.uid, 'gid': member.gid}
            if member.isfile():
                total += member.size
                require(0 <= member.size <= 16 * 1024**3 and total <= 64 * 1024**3, 'ARCHIVE_SIZE_REJECTED')
                checksum = hashlib.sha256()
                count = 0
                reader = archive.extractfile(member)
                for block in iter(lambda: reader.read(1024 * 1024), b''):
                    count += len(block)
                    checksum.update(block)
                require(count == member.size, 'ARCHIVE_TRUNCATED')
                common.update(size=count, sha256=checksum.hexdigest())
            result[name] = common
    for name, entry in result.items():
        for parent in PurePosixPath(name).parents:
            require(str(parent) not in result or result[str(parent)]['type'] == 'directory', 'ARCHIVE_FILE_AS_PARENT')
    return result


def validate_manifest(manifest):
    require(manifest.get('format') == 'boyankb-tencent-snapshot-v1' and manifest.get('source_project') == 'weknora-local', 'SOURCE_MANIFEST_REJECTED')
    require(manifest.get('consistency') == 'application-quiesced' and manifest.get('snapshot_validated') is True and manifest.get('writers_resumed') is True, 'QUIESCED_COMPLETED_SNAPSHOT_REQUIRED')
    require(set(manifest.get('components', {})) == COMPONENTS, 'COMPONENT_SET_MISMATCH')
    require(manifest.get('database_fingerprint') == fingerprint(manifest['database']), 'DATABASE_MANIFEST_FINGERPRINT_MISMATCH')
    require(manifest['database']['migration'].get('dirty') is False, 'DATABASE_MIGRATION_DIRTY')
    require(manifest['content']['enterprise']['documents']['count'] == 19, 'ENTERPRISE_DOCUMENT_COUNT_MISMATCH')
    require(set(manifest.get('images', {})) == SERVICES - {'gateway'} | {'trial-gateway'}, 'SOURCE_IMAGE_SET_MISMATCH')
    for name, entry in manifest['components'].items():
        require(isinstance(entry.get('size'), int) and entry['size'] >= 0 and re.fullmatch(r'[0-9a-f]{64}', entry.get('sha256', '')), 'COMPONENT_DIGEST_INVALID')
        if name in ARCHIVES:
            require(entry.get('inventory') == name + '.inventory.json' and re.fullmatch(r'[0-9a-f]{64}', entry.get('inventory_sha256', '')), 'INVENTORY_REFERENCE_REJECTED')


def verify_snapshot(expected_hash):
    require(re.fullmatch(r'[0-9a-f]{64}', expected_hash or '') is not None, 'EXPECTED_MANIFEST_SHA256_REQUIRED')
    manifest_path = safe_path(SOURCE / 'manifest.json', True)
    require(file_digest(manifest_path)['sha256'] == expected_hash, 'MANIFEST_SHA256_MISMATCH')
    manifest = json.loads(manifest_path.read_bytes())
    validate_manifest(manifest)
    inventories = {}
    for name, entry in manifest['components'].items():
        path = safe_path(SOURCE / name, True)
        require(file_digest(path) == {'size': entry['size'], 'sha256': entry['sha256']}, 'COMPONENT_SHA256_MISMATCH')
        if name in ARCHIVES:
            expected = json.loads(safe_path(SOURCE / entry['inventory'], True).read_bytes())
            require(fingerprint(expected) == entry['inventory_sha256'] and len(expected) == entry['members'], 'INVENTORY_SHA256_MISMATCH')
            with gzip.open(path, 'rb') as stream:
                actual = inventory(stream)
                while stream.read(1024 * 1024):
                    pass
            require(actual == expected, 'ARCHIVE_INVENTORY_MISMATCH')
            if name == 'ollama-models.tar.gz':
                require(all(item == 'models' or item.startswith('models/') for item in actual), 'OLLAMA_IDENTITY_KEYS_REJECTED')
            inventories[name] = expected
        emit('snapshot_component_verified', component=name)
    with (SOURCE / 'database.dump').open('rb') as stream:
        require(stream.read(5) == b'PGDMP', 'DATABASE_DUMP_HEADER_INVALID')
    counts = json.loads((SOURCE / 'source-counts.json').read_bytes())
    require(counts == {'database': manifest['database'], 'content': manifest['content']}, 'SOURCE_COUNTS_MISMATCH')
    values = parse_environment((SOURCE / 'runtime.env').read_bytes())
    return manifest, inventories, values


def clean_environment(values):
    return {key: value for key, value in os.environ.items() if key not in values and not key.startswith(('COMPOSE_', 'DOCKER_'))}


def compose_args(env_file):
    return ['docker', 'compose', '--project-name', PROJECT, '--project-directory', str(ROOT), '--env-file', str(env_file), '-f', str(ROOT / 'docker-compose.tencent.yml')]


def validate_configuration(config, manifest, locked):
    services = config.get('services', {})
    require(config.get('name') == PROJECT and set(services) == SERVICES and set(config.get('volumes', {})) == VOLUMES, 'COMPOSE_PROJECT_LAYOUT_MISMATCH')
    for name, volume in config['volumes'].items():
        require(volume.get('name') == PROJECT + '_' + name and not volume.get('external') and not volume.get('driver_opts') and volume.get('driver', 'local') == 'local', 'COMPOSE_VOLUME_REJECTED')
    ports = []
    for name, service in services.items():
        source_name = 'trial-gateway' if name == 'gateway' else name
        require(service.get('image') in locked and locked[service['image']] == manifest['images'][source_name]['id'], 'COMPOSE_IMAGE_LOCK_MISMATCH')
        require(not any(service.get(key) for key in ('build', 'privileged', 'devices', 'volumes_from', 'network_mode', 'container_name', 'pid', 'ipc', 'configs', 'secrets', 'use_api_socket', 'env_file')), 'COMPOSE_HOST_ACCESS_REJECTED')
        require(service.get('platform') == 'linux/amd64' and service.get('pull_policy') == 'never' and 'no-new-privileges:true' in service.get('security_opt', []), 'COMPOSE_EXECUTION_POLICY_REJECTED')
        require(float(service.get('cpus', 0)) > 0 and int(service.get('mem_limit', 0)) > 0 and int(service.get('pids_limit', 0)) > 0, 'COMPOSE_RESOURCE_LIMIT_REQUIRED')
        observed_binds, observed_volumes = {}, {}
        for mount in service.get('volumes', []):
            if mount['type'] == 'bind':
                expected = BINDS.get(name, {}).get(mount['target'])
                require(expected and mount['source'] == str(ROOT / expected) and mount.get('read_only'), 'COMPOSE_BIND_REJECTED')
                safe_path(ROOT / expected, required=False)
                observed_binds[mount['target']] = expected
            else:
                require(mount['type'] == 'volume' and mount['source'] in VOLUMES and not mount.get('volume', {}).get('subpath'), 'COMPOSE_VOLUME_MOUNT_REJECTED')
                observed_volumes[mount['target']] = mount['source']
        require(observed_binds == BINDS.get(name, {}) and observed_volumes == MOUNTS.get(name, {}), 'COMPOSE_MOUNTS_MISMATCH')
        ports.extend((name, port) for port in service.get('ports', []))
    require(len(ports) == 1 and ports[0][0] == 'gateway' and ports[0][1]['host_ip'] == '127.0.0.1' and str(ports[0][1]['published']) == '18080' and ports[0][1]['target'] == 8090, 'LOOPBACK_GATEWAY_ONLY_REQUIRED')
    for name, network in config['networks'].items():
        require(not network.get('external') and network.get('name') == PROJECT + '_' + name, 'COMPOSE_NETWORK_REJECTED')
    require(set(config['networks']) == {'edge', 'web', 'backend', 'outbound', 'graph', 'render'}, 'COMPOSE_NETWORK_SET_MISMATCH')
    require(all(config['networks'][name].get('internal') for name in ('web', 'backend', 'graph', 'render')), 'INTERNAL_NETWORK_REQUIRED')
    expected_networks = {'gateway': {'edge', 'web'}, 'frontend': {'web'}, 'app': {'web', 'backend', 'outbound', 'graph', 'render'}, 'postgres': {'backend'}, 'redis': {'backend'}, 'ollama': {'backend'}, 'docreader': {'backend'}, 'neo4j': {'graph'}, 'document-renderer': {'render'}}
    require(all(set(services[name]['networks']) == networks for name, networks in expected_networks.items()), 'SERVICE_NETWORK_SCOPE_MISMATCH')
    app = services['app']['environment']
    require(all(app.get(key) == value for key, value in {'AUTO_MIGRATE': 'false', 'AUTO_RECOVER_DIRTY': 'false', 'WEKNORA_SANDBOX_DOCKER_ENABLED': 'false', 'DISABLE_REGISTRATION': 'true'}.items()), 'APPLICATION_POLICY_MISMATCH')


def image_and_compose_guard(manifest, values):
    lock = json.loads(safe_path(ROOT / 'deployment/tencent/images.lock.json').read_bytes())
    require(lock.get('platform') == 'linux/amd64' and len(lock['images']) == 9, 'TARGET_IMAGE_LOCK_INVALID')
    locked = {item['tag'] + '@' + item['id']: item['id'] for item in lock['images']}
    require(set(locked.values()) == {item['id'] for item in manifest['images'].values()}, 'SOURCE_TARGET_IMAGE_IDS_DIFFER')
    for reference, wanted in locked.items():
        actual = json.loads(command(['docker', 'image', 'inspect', reference]))
        require(len(actual) == 1 and actual[0]['Id'] == wanted and actual[0]['Os'] == 'linux' and actual[0]['Architecture'] == 'amd64', 'LOADED_IMAGE_ID_MISMATCH')
    safe_path(ROOT / 'docker-compose.tencent.yml')
    config = json.loads(command(compose_args(SOURCE / 'runtime.env') + ['config', '--format', 'json'], env=clean_environment(values)))
    validate_configuration(config, manifest, locked)
    return config


def copy_identical(source, target):
    safe_path(target, required=False)
    if target.exists():
        safe_path(target, True)
        require(file_digest(source) == file_digest(target), 'EXISTING_CONFIGURATION_DIFFERS')
        return
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with source.open('rb') as reader, os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as writer:
        shutil.copyfileobj(reader, writer)
        writer.flush()
        os.fsync(writer.fileno())


def prepare_service_files():
    paths = [(safe_path(ROOT / relative), group) for relative, group in SERVICE_FILE_GROUPS.items()]
    for path, group in paths:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            details = os.fstat(descriptor)
            require(stat.S_ISREG(details.st_mode) and details.st_uid == 0 and details.st_nlink == 1, 'SERVICE_CONFIGURATION_FILE_REJECTED')
            os.fchown(descriptor, 0, group)
            os.fchmod(descriptor, 0o640)
            details = os.fstat(descriptor)
            require(details.st_uid == 0 and details.st_gid == group and stat.S_IMODE(details.st_mode) == 0o640, 'SERVICE_CONFIGURATION_PERMISSION_MISMATCH')
        finally:
            os.close(descriptor)


def helper_args(image, volume, script, readonly=False):
    require(volume in {PROJECT + '_' + value for value in VOLUMES} and re.fullmatch(r'sha256:[a-f0-9]{64}', image), 'HELPER_IMAGE_OR_VOLUME_REJECTED')
    mount = 'type=volume,src=' + volume + ',dst=/restore,volume-nocopy' + (',readonly' if readonly else '')
    return ['docker', '--host', 'unix:///var/run/docker.sock', 'run', '--rm', '-i', '--pull', 'never', '--network', 'none', '--read-only', '--user', '0:0', '--security-opt', 'no-new-privileges:true', '--cap-drop', 'ALL', '--cap-add', 'CHOWN', '--cap-add', 'DAC_OVERRIDE', '--cap-add', 'FOWNER', '--cap-add', 'FSETID', '--memory', '384m', '--cpus', '1', '--pids-limit', '64', '--mount', mount, '--entrypoint', '/bin/sh', image, '-ec', script]


def restore_volume(image, archive_name, expected):
    logical, member = ARCHIVES[archive_name]
    volume = PROJECT + '_' + logical
    script = 'test -z "$(find /restore -mindepth 1 -maxdepth 1 -print -quit)"; exec tar --numeric-owner --same-owner --same-permissions -xzf - -C /restore'
    with (SOURCE / archive_name).open('rb') as stream:
        command(helper_args(image, volume, script), stdin=stream, timeout=900)
    args = helper_args(image, volume, 'exec tar --numeric-owner -cf - -C /restore ' + member, True)
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        actual = inventory(process.stdout)
        while process.stdout.read(1024 * 1024):
            pass
        require(process.wait(timeout=120) == 0, 'RESTORED_VOLUME_READ_FAILED')
        require(actual == expected, 'RESTORED_VOLUME_INVENTORY_MISMATCH')
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def service_container(name):
    ids = command(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + PROJECT, '--filter', 'label=com.docker.compose.service=' + name]).decode().split()
    require(len(ids) == 1, 'EXACT_CLOUD_SERVICE_REQUIRED')
    item = json.loads(command(['docker', 'inspect', ids[0]]))[0]
    require(item['Config']['Labels'].get('com.docker.compose.project') == PROJECT and item['Config']['Labels'].get('com.docker.compose.service') == name, 'SERVICE_IDENTITY_MISMATCH')
    return item


def pg_args(container, executable, options):
    require(executable in ('psql', 'pg_restore'), 'POSTGRES_COMMAND_REJECTED')
    script = 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec ' + executable + ' --host=127.0.0.1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" "$@"'
    return ['docker', 'exec', '-i', container['Id'], 'sh', '-ec', script, 'restore', *options]


def pg_query(container, sql):
    return command(pg_args(container, 'psql', ['-X', '-A', '-t', '-v', 'ON_ERROR_STOP=1']), (sql + '\n').encode()).decode().strip()


def require_empty_database(postgres):
    tables = json.loads(pg_query(postgres, """SELECT coalesce(json_agg(json_build_object('name',c.relname,'extension',(
SELECT e.extname FROM pg_depend d JOIN pg_extension e ON e.oid=d.refobjid
WHERE d.classid='pg_class'::regclass AND d.objid=c.oid AND d.refclassid='pg_extension'::regclass AND d.deptype='e' LIMIT 1
))),'[]') FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind IN ('r','p','f');"""))
    require(all(table == {'name': 'spatial_ref_sys', 'extension': 'postgis'} for table in tables), 'RESTORE_DATABASE_NOT_EMPTY')


def preinitialized_schemas(postgres):
    state = json.loads(pg_query(postgres, """SELECT json_build_object(
'schemas',(SELECT json_agg(nspname) FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname<>'information_schema'),
'extensions',(SELECT json_agg(extname) FROM pg_extension),
'unowned_tables',(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','f','S')
AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass AND d.objid=c.oid AND d.refclassid='pg_extension'::regclass AND d.deptype='e')),
'unowned_functions',(SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'
AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.refclassid='pg_extension'::regclass AND d.deptype='e')));"""))
    schemas, extensions = set(state['schemas'] or []), set(state['extensions'] or [])
    require(schemas.issubset({'public', 'paradedb', 'pdb', 'tiger', 'topology'}), 'PREINITIALIZED_SCHEMA_REJECTED')
    require(state['unowned_tables'] == 0 and state['unowned_functions'] == 0, 'PREINITIALIZED_OBJECT_REJECTED')
    required = {'paradedb': 'pg_search', 'pdb': 'pg_search', 'tiger': 'postgis_tiger_geocoder', 'topology': 'postgis_topology'}
    require(all(required[name] in extensions for name in schemas if name in required), 'PREINITIALIZED_EXTENSION_MISSING')
    return schemas.intersection({'paradedb', 'tiger', 'topology'})


def filter_schema_toc(toc, existing):
    require(set(existing).issubset({'paradedb', 'tiger', 'topology'}), 'SCHEMA_FILTER_REJECTED')
    lines, skipped = [], []
    for line in toc.decode('utf-8').splitlines():
        match = re.fullmatch(r'\d+; 2615 \d+ SCHEMA - ([A-Za-z_][A-Za-z_0-9]*) \S+', line)
        if match and match[1] in existing:
            require(match[1] not in skipped, 'SCHEMA_TOC_DUPLICATE')
            skipped.append(match[1])
        else:
            lines.append(line)
    require(set(skipped) == set(existing), 'SCHEMA_TOC_MISMATCH')
    return ('\n'.join(lines) + '\n').encode(), sorted(skipped)


def restore_database(postgres):
    require_empty_database(postgres)
    existing = preinitialized_schemas(postgres)
    with (SOURCE / 'database.dump').open('rb') as stream:
        listing = command(['docker', 'exec', '-i', postgres['Id'], 'pg_restore', '--list'], stdin=stream)
    toc, skipped = filter_schema_toc(listing, existing)
    target = '/tmp/boyankb-restore-' + uuid.uuid4().hex + '.list'
    command(['docker', 'exec', '-i', postgres['Id'], 'sh', '-ec', 'umask 077; set -C; cat > "$1"', 'restore', target], toc)
    with (SOURCE / 'database.dump').open('rb') as stream:
        command(pg_args(postgres, 'pg_restore', ['--exit-on-error', '--single-transaction', '--no-owner', '--no-acl', '--use-list=' + target]), stdin=stream, timeout=900)
    return skipped


def database_scope(container):
    tables = json.loads(pg_query(container, "SELECT coalesce(json_agg(tablename ORDER BY tablename),'[]') FROM pg_tables WHERE schemaname='public';"))
    require('schema_migrations' in tables and all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', table) for table in tables), 'RESTORED_TABLE_SET_INVALID')
    queries = ['SELECT \'' + table + '\' AS name,count(*) AS total FROM "' + table + '"' for table in tables]
    counts = json.loads(pg_query(container, 'SELECT json_object_agg(name,total) FROM (' + ' UNION ALL '.join(queries) + ') q;'))
    migration = json.loads(pg_query(container, 'SELECT row_to_json(m) FROM schema_migrations m LIMIT 1;'))
    return {'migration': migration, 'row_counts': counts, 'postgres_version': pg_query(container, 'SHOW server_version;')}


def content_counts(container):
    queries = {
        'documents': "SELECT count(*) AS count, md5(string_agg(id||':'||coalesce(file_hash,'')||':'||parse_status,',' ORDER BY id)) AS digest FROM knowledges WHERE deleted_at IS NULL",
        'chunks': "SELECT count(*) AS count, sum(length(content)) AS characters, md5(string_agg(id||':'||md5(content),',' ORDER BY id)) AS digest FROM chunks WHERE deleted_at IS NULL",
        'embeddings': "SELECT count(*) AS count, md5(string_agg(id::text||':'||chunk_id||':'||md5(content)||':'||md5(embedding::text),',' ORDER BY id)) AS digest FROM embeddings WHERE true",
    }
    return {scope: {name: json.loads(pg_query(container, 'SELECT row_to_json(q) FROM (' + sql + condition + ') q;')) for name, sql in queries.items()} for scope, condition in (('all', ''), ('enterprise', " AND knowledge_base_id='" + KB + "'"))}


def write_report(path, report, replace=False):
    safe_path(path, required=False)
    require(replace or not path.exists(), 'RESTORE_REPORT_ALREADY_EXISTS')
    if path.exists():
        safe_path(path, True)
    temporary = path.with_name(path.name + '.next')
    require(not temporary.exists(), 'RESTORE_REPORT_TEMP_EXISTS')
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
        stream.write(canonical(report) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def apply_restore(manifest, inventories, values, manifest_hash, report_path):
    fresh_target()
    sentinel = ROOT / '.tencent-restore-started.json'
    safe_path(sentinel, required=False)
    with os.fdopen(os.open(sentinel, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
        stream.write(canonical({'project': PROJECT, 'manifest_sha256': manifest_hash}) + b'\n')
    report = {'format': 'boyankb-tencent-restore-v1', 'project': PROJECT, 'manifest_sha256': manifest_hash, 'phase': 'preparing', 'status': 'running', 'volumes_verified': [], 'database_counts_verified': False, 'database_content_verified': False, 'models_verified': False, 'neo4j_offline_consistency_verified': False, 'graph_counts_verified': False, 'public_entry_opened': False}
    write_report(report_path, report)
    try:
        for source, target in (('runtime.env', '.env.tencent'), ('config.yaml', 'config/config.yaml'), ('builtin_models.local.yaml', 'config/builtin_models.local.yaml')):
            copy_identical(SOURCE / source, ROOT / target)
            entry = manifest['components'][source]
            require(file_digest(ROOT / target) == {'size': entry['size'], 'sha256': entry['sha256']}, 'COPIED_CONFIGURATION_DIGEST_MISMATCH')
        prepare_service_files()
        fresh_target()
        for logical in sorted(VOLUMES):
            volume = PROJECT + '_' + logical
            command(['docker', 'volume', 'create', '--driver', 'local', '--label', 'com.docker.compose.project=' + PROJECT, '--label', 'com.docker.compose.volume=' + logical, '--label', 'boyankb.restore.manifest-sha256=' + manifest_hash, volume])
            created = json.loads(command(['docker', 'volume', 'inspect', volume]))[0]
            labels = created.get('Labels', {})
            require(created.get('Name') == volume and created.get('Driver') == 'local' and not created.get('Options') and labels.get('com.docker.compose.project') == PROJECT and labels.get('com.docker.compose.volume') == logical and labels.get('boyankb.restore.manifest-sha256') == manifest_hash, 'CREATED_VOLUME_IDENTITY_MISMATCH')
        report['phase'] = 'restoring_volumes'
        write_report(report_path, report, True)
        helper_image = manifest['images']['postgres']['id']
        for archive_name in ARCHIVES:
            emit('volume_restore_started', component=archive_name)
            restore_volume(helper_image, archive_name, inventories[archive_name])
            report['volumes_verified'].append(ARCHIVES[archive_name][0])
            write_report(report_path, report, True)
            emit('volume_restore_verified', verified_volumes=len(report['volumes_verified']))
        report['phase'] = 'restoring_database'
        write_report(report_path, report, True)
        compose = compose_args(ROOT / '.env.tencent')
        environment = clean_environment(values)
        command(compose + ['up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '600', 'postgres'], env=environment, timeout=660)
        postgres = service_container('postgres')
        require(postgres['Image'] == manifest['images']['postgres']['id'] and postgres['State'].get('Health', {}).get('Status') == 'healthy', 'POSTGRES_NOT_HEALTHY_OR_WRONG_IMAGE')
        report['preinitialized_schemas_skipped'] = restore_database(postgres)
        require(database_scope(postgres) == manifest['database'], 'RESTORED_DATABASE_COUNTS_MISMATCH')
        require(content_counts(postgres) == manifest['content'], 'RESTORED_DATABASE_CONTENT_MISMATCH')
        report.update(database_counts_verified=True, database_content_verified=True, phase='starting_services')
        write_report(report_path, report, True)
        emit('database_restore_verified', tables=len(manifest['database']['row_counts']))
        command(compose + ['up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '600'], env=environment, timeout=660)
        running = {name: service_container(name) for name in sorted(SERVICES)}
        require(all(item['State'].get('Running') and item['State'].get('Health', {}).get('Status') == 'healthy' and item['Image'] == manifest['images']['trial-gateway' if name == 'gateway' else name]['id'] for name, item in running.items()), 'RESTORED_SERVICES_NOT_HEALTHY')
        require(content_counts(running['postgres']) == manifest['content'], 'CONTENT_CHANGED_AFTER_START')
        tags = json.loads(command(['docker', 'exec', running['app']['Id'], 'curl', '-fsS', 'http://ollama:11434/api/tags']))
        models = [{key: model[key] for key in ('name', 'digest', 'size')} for model in tags.get('models', [])]
        require(sorted(models, key=lambda item: item['name']) == sorted(manifest['models'], key=lambda item: item['name']), 'RESTORED_MODEL_INVENTORY_MISMATCH')
        report.update(status='verified', phase='completed', models_verified=True, healthy_services=len(running), database_counts_verified_before_application_start=True, volume_inventory_verified_before_service_start=True, application_restore_verified=False, application_runtime_started=True, runtime_restore_verified=True)
        write_report(report_path, report, True)
        emit('tencent_restore_verified', healthy_services=len(running), verified_volumes=4, database_verified=True, models_verified=True, graph_counts_verified=False, public_entry_opened=False)
    except BaseException:
        report['status'] = 'failed_resources_retained'
        write_report(report_path, report, True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-manifest-sha256', required=True)
    parser.add_argument('--restore-new-volumes', action='store_true')
    parser.add_argument('--report', type=Path, default=ROOT / 'tencent-restore.report.json')
    args = parser.parse_args(argv)
    try:
        host_guard()
        fresh_target()
        manifest, inventories, values = verify_snapshot(args.expected_manifest_sha256)
        image_and_compose_guard(manifest, values)
        total = sum(item.get('size', 0) for inventory_items in inventories.values() for item in inventory_items.values()) + manifest['components']['database.dump']['size'] * 4
        require(shutil.disk_usage('/var/lib/docker').free > total + 4 * 1024**3, 'INSUFFICIENT_DOCKER_FREE_SPACE')
        if not args.apply:
            emit('tencent_restore_preflight_verified', components=len(COMPONENTS), new_volumes=len(VOLUMES), runtime_changed=False, restore_verified=False)
            return 0
        require(args.restore_new_volumes, 'RESTORE_NEW_VOLUMES_FLAG_REQUIRED')
        apply_restore(manifest, inventories, values, args.expected_manifest_sha256, args.report)
        return 0
    except (RestoreError, OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.SubprocessError) as error:
        emit('tencent_restore_failed', code=str(error) if isinstance(error, RestoreError) else 'LOCAL_STATE_OR_COMMAND_INVALID', resources_deleted=False)
        return 1
    except KeyboardInterrupt:
        emit('tencent_restore_interrupted', resources_deleted=False)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
