import copy
import importlib.util
import io
import json
from pathlib import Path
import stat
import tarfile
from types import SimpleNamespace
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location('tencent_restore', Path(__file__).with_name('tencent-restore.py'))
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def tar_stream(entries):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name, kind, mode, uid, payload in entries:
            entry = tarfile.TarInfo(name)
            entry.type, entry.mode, entry.uid, entry.gid = kind, mode, uid, uid
            entry.size = len(payload) if kind == tarfile.REGTYPE else 0
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                entry.linkname = '/etc/passwd'
            archive.addfile(entry, io.BytesIO(payload) if kind == tarfile.REGTYPE else None)
    stream.seek(0)
    return stream


def manifest():
    database = {'migration': {'version': 114, 'dirty': False}, 'row_counts': {'users': 2}, 'postgres_version': '17.9'}
    components = {name: {'size': 100, 'sha256': 'a' * 64} for name in MOD.COMPONENTS}
    for name in MOD.ARCHIVES:
        components[name].update(inventory=name + '.inventory.json', inventory_sha256='b' * 64, members=1)
    return {'format': 'boyankb-tencent-snapshot-v1', 'source_project': 'weknora-local', 'consistency': 'application-quiesced', 'snapshot_validated': True, 'writers_resumed': True, 'components': components, 'database': database, 'database_fingerprint': MOD.fingerprint(database), 'content': {'enterprise': {'documents': {'count': 19}}}, 'images': {name: {'id': 'sha256:' + format(index, '064x')} for index, name in enumerate(sorted(MOD.SERVICES - {'gateway'} | {'trial-gateway'}), 1)}}


def config_fixture(source):
    networks = {'gateway': {'edge', 'web'}, 'frontend': {'web'}, 'app': {'web', 'backend', 'outbound', 'graph', 'render'}, 'postgres': {'backend'}, 'redis': {'backend'}, 'ollama': {'backend'}, 'docreader': {'backend'}, 'neo4j': {'graph'}, 'document-renderer': {'render'}}
    config = {'name': MOD.PROJECT, 'volumes': {name: {'name': MOD.PROJECT + '_' + name} for name in MOD.VOLUMES}, 'networks': {name: {'name': MOD.PROJECT + '_' + name, 'internal': name not in ('edge', 'outbound')} for name in ('edge', 'web', 'backend', 'outbound', 'graph', 'render')}, 'services': {}}
    locked = {}
    for name in MOD.SERVICES:
        source_name = 'trial-gateway' if name == 'gateway' else name
        identity = source['images'][source_name]['id']
        reference = 'synthetic/' + name + ':pinned@' + identity
        locked[reference] = identity
        mounts = [{'type': 'volume', 'source': logical, 'target': target} for target, logical in MOD.MOUNTS.get(name, {}).items()]
        mounts.extend({'type': 'bind', 'source': str(MOD.ROOT / relative), 'target': target, 'read_only': True} for target, relative in MOD.BINDS.get(name, {}).items())
        config['services'][name] = {'image': reference, 'platform': 'linux/amd64', 'pull_policy': 'never', 'security_opt': ['no-new-privileges:true'], 'cpus': 1, 'mem_limit': 1000000, 'pids_limit': 64, 'volumes': mounts, 'networks': {key: {} for key in networks[name]}}
    config['services']['gateway']['ports'] = [{'host_ip': '127.0.0.1', 'published': '18080', 'target': 8090}]
    config['services']['app']['environment'] = {'AUTO_MIGRATE': 'false', 'AUTO_RECOVER_DIRTY': 'false', 'WEKNORA_SANDBOX_DOCKER_ENABLED': 'false', 'DISABLE_REGISTRATION': 'true'}
    return config, locked


class TencentRestoreTests(unittest.TestCase):
    def test_non_linux_rejected_without_docker(self):
        with mock.patch.object(MOD.sys, 'platform', 'win32'), mock.patch.object(MOD, 'command') as command:
            with self.assertRaisesRegex(MOD.RestoreError, 'UBUNTU_ROOT_AMD64_REQUIRED'):
                MOD.host_guard()
            command.assert_not_called()

    def test_existing_labeled_project_rejected(self):
        with mock.patch.object(MOD, 'command', return_value=b'existing\n'):
            with self.assertRaisesRegex(MOD.RestoreError, 'TARGET_PROJECT_ALREADY_EXISTS'):
                MOD.fresh_target()

    def test_existing_unlabeled_named_volume_rejected(self):
        with mock.patch.object(MOD, 'command', side_effect=[b'', b'', b'', b'unrelated-container\n', b'boyankb-tencent_neo4j-data\n']):
            with self.assertRaisesRegex(MOD.RestoreError, 'TARGET_NAMED_RESOURCE_ALREADY_EXISTS'):
                MOD.fresh_target()

    def test_source_project_resources_are_not_removed(self):
        with mock.patch.object(MOD, 'command', side_effect=[b'', b'', b'', b'weknora-local-app-1\n', b'weknora-local_data-files\n', b'weknora-local_default\n']) as command:
            MOD.fresh_target()
            self.assertFalse(any('rm' in call.args[0] or 'prune' in call.args[0] for call in command.call_args_list))

    def test_manifest_component_and_database_integrity(self):
        value = manifest()
        MOD.validate_manifest(value)
        value['database']['row_counts']['users'] = 3
        with self.assertRaisesRegex(MOD.RestoreError, 'FINGERPRINT'):
            MOD.validate_manifest(value)

    def test_manifest_rejects_missing_component_and_unquiesced_copy(self):
        for key in ('database.dump', 'neo4j-data.tar.gz', 'runtime.env'):
            value = manifest()
            del value['components'][key]
            with self.assertRaisesRegex(MOD.RestoreError, 'COMPONENT_SET'):
                MOD.validate_manifest(value)
        value = manifest()
        value['consistency'] = 'online'
        with self.assertRaisesRegex(MOD.RestoreError, 'QUIESCED'):
            MOD.validate_manifest(value)

    def test_inventory_preserves_hash_ownership_and_directory_mode(self):
        result = MOD.inventory(tar_stream([('.', tarfile.DIRTYPE, 0o2775, 7474, b''), ('./file', tarfile.REGTYPE, 0o640, 999, b'synthetic-content')]))
        self.assertEqual(result['.'], {'type': 'directory', 'mode': 0o2775, 'uid': 7474, 'gid': 7474})
        self.assertEqual(result['file']['uid'], 999)
        self.assertEqual(result['file']['mode'], 0o640)
        self.assertEqual(result['file']['sha256'], MOD.hashlib.sha256(b'synthetic-content').hexdigest())

    def test_inventory_rejects_path_traversal_and_duplicates(self):
        for name in ('../outside', '/etc/passwd', 'a\\b', 'C:escape', 'line\nbreak'):
            with self.assertRaises(MOD.RestoreError):
                MOD.inventory(tar_stream([(name, tarfile.REGTYPE, 0o600, 0, b'x')]))
        with self.assertRaisesRegex(MOD.RestoreError, 'DUPLICATE'):
            MOD.inventory(tar_stream([('a', tarfile.REGTYPE, 0o600, 0, b'x'), ('./a', tarfile.REGTYPE, 0o600, 0, b'x')]))

    def test_inventory_rejects_links_devices_bad_uid_and_suid(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE):
            with self.assertRaisesRegex(MOD.RestoreError, 'SPECIAL_FILE'):
                MOD.inventory(tar_stream([('bad', kind, 0o600, 0, b'')]))
        with self.assertRaisesRegex(MOD.RestoreError, 'OWNERSHIP'):
            MOD.inventory(tar_stream([('bad', tarfile.REGTYPE, 0o600, 70000, b'x')]))
        with self.assertRaisesRegex(MOD.RestoreError, 'PERMISSION'):
            MOD.inventory(tar_stream([('bad', tarfile.REGTYPE, 0o4755, 0, b'x')]))

    def test_inventory_rejects_file_parent_in_either_order(self):
        entries = [('a', tarfile.REGTYPE, 0o600, 0, b'x'), ('a/b', tarfile.REGTYPE, 0o600, 0, b'x')]
        for sequence in (entries, list(reversed(entries))):
            with self.assertRaisesRegex(MOD.RestoreError, 'FILE_AS_PARENT'):
                MOD.inventory(tar_stream(sequence))

    def test_runtime_environment_exact_allowlist_and_secret_preservation(self):
        values = {key: 'synthetic' for key in MOD.ENV_KEYS}
        values.update(COMPOSE_PROJECT_NAME=MOD.PROJECT, PUBLIC_HOST=MOD.PUBLIC_IP, PUBLIC_ORIGIN='http://' + MOD.PUBLIC_IP, DB_USER='weknora', DB_NAME='weknora', SYSTEM_AES_KEY='a' * 32, DB_PASSWORD="apostrophe' dollar$ preserved")
        raw = ('\n'.join(key + "='" + value.replace("'", "\\'") + "'" for key, value in values.items()) + '\n').encode()
        self.assertEqual(MOD.parse_environment(raw), values)
        with self.assertRaisesRegex(MOD.RestoreError, 'KEY_REJECTED'):
            MOD.parse_environment(raw + b"DOCKER_HOST='tcp://unexpected'\n")

    def test_valid_compose_configuration(self):
        source = manifest()
        config, locked = config_fixture(source)
        with mock.patch.object(MOD, 'safe_path'):
            MOD.validate_configuration(config, source, locked)

    def test_compose_rejects_public_database_ports_or_foreign_volumes(self):
        source = manifest()
        for mutation in ('port', 'volume', 'host', 'socket', 'sandbox'):
            config, locked = config_fixture(source)
            if mutation == 'port':
                config['services']['postgres']['ports'] = [{'host_ip': '0.0.0.0', 'published': '5432', 'target': 5432}]
            elif mutation == 'volume':
                config['volumes']['data-files']['name'] = 'weknora-local_data-files'
            elif mutation == 'host':
                config['services']['app']['network_mode'] = 'host'
            elif mutation == 'socket':
                config['services']['app']['volumes'].append({'type': 'bind', 'source': '/var/run/docker.sock', 'target': '/var/run/docker.sock', 'read_only': True})
            else:
                config['services']['app']['environment']['WEKNORA_SANDBOX_DOCKER_ENABLED'] = 'true'
            with mock.patch.object(MOD, 'safe_path'), self.assertRaises(MOD.RestoreError):
                MOD.validate_configuration(config, source, locked)

    def test_helper_is_isolated_and_never_autopopulates_volumes(self):
        args = MOD.helper_args('sha256:' + 'a' * 64, MOD.PROJECT + '_neo4j-data', 'synthetic', True)
        self.assertIn('none', args)
        self.assertIn('-i', args)
        self.assertIn('--read-only', args)
        self.assertIn('0:0', args)
        self.assertIn('type=volume,src=boyankb-tencent_neo4j-data,dst=/restore,volume-nocopy,readonly', args)
        self.assertNotIn('--privileged', args)
        self.assertNotIn('--publish', args)
        with self.assertRaisesRegex(MOD.RestoreError, 'HELPER'):
            MOD.helper_args('sha256:' + 'a' * 64, 'weknora-local_neo4j-data', 'synthetic')

    def test_pg_restore_uses_tcp_original_user_and_single_transaction(self):
        args = MOD.pg_args({'Id': 'synthetic'}, 'pg_restore', ['--exit-on-error', '--single-transaction', '--no-owner', '--no-acl'])
        self.assertIn('--single-transaction', args)
        self.assertIn('--exit-on-error', args)
        self.assertIn('--host=127.0.0.1', args[6])
        self.assertIn('"$POSTGRES_USER"', args[6])
        self.assertNotIn('--clean', args)

    def test_extension_empty_database_guard_rejects_business_table(self):
        with mock.patch.object(MOD, 'pg_query', return_value=json.dumps([{'name': 'spatial_ref_sys', 'extension': 'postgis'}])):
            MOD.require_empty_database({})
        with mock.patch.object(MOD, 'pg_query', return_value=json.dumps([{'name': 'users', 'extension': None}])):
            with self.assertRaisesRegex(MOD.RestoreError, 'NOT_EMPTY'):
                MOD.require_empty_database({})

    def test_preinitialized_schema_requires_expected_extensions(self):
        state = {'schemas': ['public', 'paradedb', 'pdb', 'tiger', 'topology'], 'extensions': ['pg_search', 'postgis_tiger_geocoder', 'postgis_topology'], 'unowned_tables': 0, 'unowned_functions': 0}
        with mock.patch.object(MOD, 'pg_query', return_value=json.dumps(state)):
            self.assertEqual(MOD.preinitialized_schemas({}), {'paradedb', 'tiger', 'topology'})
        state['unowned_functions'] = 1
        with mock.patch.object(MOD, 'pg_query', return_value=json.dumps(state)):
            with self.assertRaisesRegex(MOD.RestoreError, 'OBJECT_REJECTED'):
                MOD.preinitialized_schemas({})

    def test_toc_filter_preserves_data_comments_and_indexes(self):
        toc = b'1; 2615 10 SCHEMA - paradedb owner\n2; 2615 11 SCHEMA - tiger owner\n3; 2615 12 SCHEMA - topology owner\n4; 0 0 COMMENT - SCHEMA topology owner\n5; 0 22 TABLE DATA public users owner\n6; 1259 23 INDEX public users_pkey owner\n'
        output, skipped = MOD.filter_schema_toc(toc, {'paradedb', 'tiger', 'topology'})
        self.assertEqual(skipped, ['paradedb', 'tiger', 'topology'])
        self.assertIn(b'TABLE DATA public users', output)
        self.assertIn(b'INDEX public users_pkey', output)
        self.assertIn(b'COMMENT - SCHEMA topology', output)
        with self.assertRaisesRegex(MOD.RestoreError, 'TOC_MISMATCH'):
            MOD.filter_schema_toc(b'', {'paradedb'})

    def test_command_never_exposes_stderr_and_pins_socket_once(self):
        with mock.patch.object(MOD.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout=b'ok')) as run:
            MOD.command(['docker', '--host', 'unix:///var/run/docker.sock', 'version'])
        self.assertEqual(run.call_args.args[0].count('--host'), 1)
        with mock.patch.object(MOD.subprocess, 'run', return_value=mock.Mock(returncode=1, stdout=b'', stderr=b'secret')):
            with self.assertRaisesRegex(MOD.RestoreError, '^COMMAND_FAILED$'):
                MOD.command(['docker', 'version'])

    def test_service_files_change_only_four_fixed_groups_and_modes(self):
        expected = [('config/config.yaml', 1000), ('config/builtin_models.local.yaml', 1000), ('deployment/tencent/Caddyfile', 65532), ('deployment/tencent/Caddyfile.ip-https', 65532)]
        states = []
        for _, group in expected:
            states.extend([SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_gid=0, st_nlink=1), SimpleNamespace(st_mode=stat.S_IFREG | 0o640, st_uid=0, st_gid=group, st_nlink=1)])
        with mock.patch.object(MOD, 'safe_path', side_effect=lambda path: path) as paths, mock.patch.object(MOD.os, 'O_NOFOLLOW', 0x20000, create=True), mock.patch.object(MOD.os, 'open', side_effect=range(10, 14)) as opened, mock.patch.object(MOD.os, 'fstat', side_effect=states), mock.patch.object(MOD.os, 'fchown', create=True) as owner, mock.patch.object(MOD.os, 'fchmod', create=True) as mode, mock.patch.object(MOD.os, 'close') as closed:
            MOD.prepare_service_files()
        self.assertEqual(paths.call_args_list, [mock.call(MOD.ROOT / relative) for relative, _ in expected])
        self.assertEqual([call.args[0] for call in opened.call_args_list], [MOD.ROOT / relative for relative, _ in expected])
        self.assertTrue(all(call.args[1] & 0x20000 for call in opened.call_args_list))
        self.assertEqual(owner.call_args_list, [mock.call(10 + index, 0, group) for index, (_, group) in enumerate(expected)])
        self.assertEqual(mode.call_args_list, [mock.call(descriptor, 0o640) for descriptor in range(10, 14)])
        self.assertEqual(closed.call_args_list, [mock.call(descriptor) for descriptor in range(10, 14)])
        self.assertFalse(any('.env' in str(call.args[0]) for call in opened.call_args_list))

    def test_service_files_validate_all_paths_before_permission_changes(self):
        calls = [MOD.ROOT / relative for relative in list(MOD.SERVICE_FILE_GROUPS)[:3]]
        with mock.patch.object(MOD, 'safe_path', side_effect=[*calls, MOD.RestoreError('SYMLINK_PATH_REJECTED')]), mock.patch.object(MOD.os, 'open') as opened:
            with self.assertRaisesRegex(MOD.RestoreError, 'SYMLINK_PATH_REJECTED'):
                MOD.prepare_service_files()
        opened.assert_not_called()

    def test_service_files_reject_nonroot_hardlinks_and_nonregular_files(self):
        for mode, owner, links in ((stat.S_IFREG | 0o600, 1000, 1), (stat.S_IFREG | 0o600, 0, 2), (stat.S_IFLNK | 0o600, 0, 1)):
            details = SimpleNamespace(st_mode=mode, st_uid=owner, st_gid=0, st_nlink=links)
            with self.subTest(mode=mode, owner=owner, links=links), mock.patch.object(MOD, 'safe_path', side_effect=lambda path: path), mock.patch.object(MOD.os, 'O_NOFOLLOW', 0x20000, create=True), mock.patch.object(MOD.os, 'open', return_value=10), mock.patch.object(MOD.os, 'fstat', return_value=details), mock.patch.object(MOD.os, 'fchown', create=True) as chown, mock.patch.object(MOD.os, 'fchmod', create=True) as chmod, mock.patch.object(MOD.os, 'close') as close:
                with self.assertRaisesRegex(MOD.RestoreError, 'SERVICE_CONFIGURATION_FILE_REJECTED'):
                    MOD.prepare_service_files()
                chown.assert_not_called()
                chmod.assert_not_called()
                close.assert_called_once_with(10)

    def test_service_files_verify_effective_permissions(self):
        details = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_gid=0, st_nlink=1)
        with mock.patch.object(MOD, 'safe_path', side_effect=lambda path: path), mock.patch.object(MOD.os, 'O_NOFOLLOW', 0x20000, create=True), mock.patch.object(MOD.os, 'open', return_value=10), mock.patch.object(MOD.os, 'fstat', return_value=details), mock.patch.object(MOD.os, 'fchown', create=True), mock.patch.object(MOD.os, 'fchmod', create=True), mock.patch.object(MOD.os, 'close') as close:
            with self.assertRaisesRegex(MOD.RestoreError, 'SERVICE_CONFIGURATION_PERMISSION_MISMATCH'):
                MOD.prepare_service_files()
        close.assert_called_once_with(10)

    def test_verify_only_cannot_apply_or_read_dump_into_database(self):
        value = manifest()
        with mock.patch.object(MOD, 'host_guard'), mock.patch.object(MOD, 'fresh_target'), mock.patch.object(MOD, 'verify_snapshot', return_value=(value, {}, {})), mock.patch.object(MOD, 'image_and_compose_guard'), mock.patch.object(MOD.shutil, 'disk_usage', return_value=mock.Mock(free=100 * 1024**3)), mock.patch.object(MOD, 'apply_restore') as apply, mock.patch.object(MOD.sys, 'stdout', io.StringIO()):
            self.assertEqual(MOD.main(['--expected-manifest-sha256', 'a' * 64]), 0)
        apply.assert_not_called()

    def test_apply_requires_new_volumes_explicit_flag(self):
        value = manifest()
        with mock.patch.object(MOD, 'host_guard'), mock.patch.object(MOD, 'fresh_target'), mock.patch.object(MOD, 'verify_snapshot', return_value=(value, {}, {})), mock.patch.object(MOD, 'image_and_compose_guard'), mock.patch.object(MOD.shutil, 'disk_usage', return_value=mock.Mock(free=100 * 1024**3)), mock.patch.object(MOD, 'apply_restore') as apply, mock.patch.object(MOD.sys, 'stdout', io.StringIO()) as output:
            self.assertEqual(MOD.main(['--apply', '--expected-manifest-sha256', 'a' * 64]), 1)
            self.assertIn('RESTORE_NEW_VOLUMES_FLAG_REQUIRED', output.getvalue())
        apply.assert_not_called()


if __name__ == '__main__':
    unittest.main()
