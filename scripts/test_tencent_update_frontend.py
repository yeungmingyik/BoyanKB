import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location('frontend_update_test_target', Path(__file__).with_name('tencent-update-frontend.py'))
UPDATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UPDATE)
OLD = 'boyankb/frontend:old@sha256:' + 'a' * 64
NEW = 'boyankb/frontend:new@sha256:' + 'b' * 64


class FrontendReleaseTests(unittest.TestCase):
    def setUp(self):
        self.configuration = {'services': {name: {'image': 'example/' + name + ':v1@sha256:' + str(index) * 64} for index, name in enumerate(sorted(UPDATE.SERVICES))}, 'networks': {'backend': {'internal': True}}}
        self.configuration['services']['frontend']['image'] = OLD
        self.lock = {'platform': 'linux/amd64', 'upstream': 'Tencent/WeKnora v0.8.2', 'images': [{'tag': item['image'].split('@')[0], 'id': item['image'].split('@')[1], 'digests': []} for item in self.configuration['services'].values()]}
        self.compose = ('name: ${COMPOSE_PROJECT_NAME:-boyankb-tencent}\nservices:\n  gateway:\n    image: example/gateway:v1\n  frontend:\n    image: ' + OLD + '\n    environment:\n      APP_HOST: 10.239.80.4\n  app:\n    image: example/app:v1\nnetworks:\n  backend:\n    internal: true\n').encode()
        self.containers = {name: {'Id': name + '-old', 'Config': {'Image': item['image'], 'Labels': {'com.docker.compose.service': name}}, 'State': {'Health': {'Status': 'healthy'}}, 'HostConfig': {'PortBindings': {'443/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '443'}]} if name == 'gateway' else {}}} for name, item in self.configuration['services'].items()}

    def test_fixed_frontend_digest_required(self):
        for reference in ('boyankb/frontend:latest', 'boyankb/app:new@sha256:' + 'b' * 64, NEW + '\n', NEW.replace('sha256:', 'sha512:'), NEW.replace('b' * 64, 'g' * 64), 'registry.invalid/' + NEW):
            with self.subTest(reference=reference), self.assertRaisesRegex(RuntimeError, 'FIXED_FRONTEND_IMAGE_REQUIRED'):
                UPDATE.split_image(reference)

    def test_image_verifies_tag_and_digest_on_amd64(self):
        image = {'Id': 'sha256:' + 'b' * 64, 'Os': 'linux', 'Architecture': 'amd64'}
        with patch.object(UPDATE, 'docker', return_value=json.dumps([image, image]).encode()) as docker:
            UPDATE.verify_image(NEW)
        docker.assert_called_once_with(['image', 'inspect', 'boyankb/frontend:new', NEW])

    def test_wrong_image_identity_and_platform_are_rejected(self):
        image = {'Id': 'sha256:' + 'b' * 64, 'Os': 'linux', 'Architecture': 'amd64'}
        for field, value in (('Id', 'sha256:' + 'c' * 64), ('Os', 'windows'), ('Architecture', 'arm64')):
            candidate = dict(image, **{field: value})
            with self.subTest(field=field), patch.object(UPDATE, 'docker', return_value=json.dumps([image, candidate]).encode()), self.assertRaisesRegex(RuntimeError, 'IMAGE_ID_OR_PLATFORM_MISMATCH'):
                UPDATE.verify_image(NEW)

    def test_only_frontend_image_and_corresponding_lock_change(self):
        original_lock = copy.deepcopy(self.lock)
        content, lock = UPDATE.updated_files(self.compose, self.lock, OLD, NEW)
        self.assertEqual(content, self.compose.replace(OLD.encode(), NEW.encode()))
        after = json.loads(lock)
        for before, updated in zip(self.lock['images'], after['images'], strict=True):
            if before['tag'] == 'boyankb/frontend:old':
                self.assertEqual(updated, {'tag': 'boyankb/frontend:new', 'id': 'sha256:' + 'b' * 64, 'digests': ['boyankb/frontend@sha256:' + 'b' * 64]})
            else:
                self.assertEqual(before, updated)
        self.assertEqual(self.lock, original_lock)
        self.assertEqual(after['upstream'], original_lock['upstream'])

    def test_other_sections_cannot_supply_missing_frontend_image(self):
        content = self.compose.replace(OLD.encode(), b'example/frontend:v2').replace(b'example/gateway:v1', OLD.encode())
        with self.assertRaisesRegex(RuntimeError, 'FRONTEND_IMAGE_LINE_MISMATCH'):
            UPDATE.updated_files(content, self.lock, OLD, NEW)

    def test_duplicate_section_or_image_line_is_rejected(self):
        for content, code in ((self.compose + b'  frontend:\n    image: duplicate\n', 'EXACT_FRONTEND_SECTION_REQUIRED'), (self.compose.replace(('    image: ' + OLD).encode(), ('    image: ' + OLD + '\n    image: ' + OLD).encode()), 'FRONTEND_IMAGE_LINE_MISMATCH')):
            with self.subTest(code=code), self.assertRaisesRegex(RuntimeError, code):
                UPDATE.updated_files(content, self.lock, OLD, NEW)

    def test_unchanged_files_are_not_mutated(self):
        content, lock = UPDATE.updated_files(self.compose, self.lock, OLD, OLD)
        self.assertEqual(content, self.compose)
        self.assertEqual(json.loads(lock)['images'][0], self.lock['images'][0])

    def test_parsed_configuration_only_allows_frontend_image(self):
        candidate = copy.deepcopy(self.configuration)
        candidate['services']['frontend']['image'] = NEW
        UPDATE.validate_change(self.configuration, candidate, NEW)
        for field in ('app_image', 'frontend_environment', 'network', 'port'):
            changed = copy.deepcopy(candidate)
            if field == 'app_image':
                changed['services']['app']['image'] = 'other-app'
            elif field == 'frontend_environment':
                changed['services']['frontend']['environment'] = {'APP_HOST': 'other-app'}
            elif field == 'network':
                changed['networks']['backend']['internal'] = False
            else:
                changed['services']['frontend']['ports'] = ['8080:80']
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, 'UNEXPECTED_CONFIGURATION_CHANGE'):
                UPDATE.validate_change(self.configuration, changed, NEW)

    def test_lock_requires_exact_nine_images(self):
        UPDATE.validate_lock(self.configuration, self.lock)
        for field in ('digest', 'duplicate', 'platform', 'service'):
            configuration = copy.deepcopy(self.configuration)
            lock = copy.deepcopy(self.lock)
            if field == 'digest':
                lock['images'][0]['id'] = 'sha256:' + 'f' * 64
            elif field == 'duplicate':
                lock['images'][-1] = lock['images'][0]
            elif field == 'platform':
                lock['platform'] = 'linux/arm64'
            else:
                del configuration['services']['redis']
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                UPDATE.validate_lock(configuration, lock)

    def test_recreation_keeps_other_eight_container_ids_and_ports(self):
        candidate = copy.deepcopy(self.containers)
        candidate['frontend']['Id'] = 'frontend-new'
        candidate['frontend']['Config']['Image'] = NEW
        UPDATE.validate_containers(self.containers, candidate, NEW)
        for service in UPDATE.SERVICES - {'frontend'}:
            changed = copy.deepcopy(candidate)
            changed[service]['Id'] += '-replacement'
            with self.subTest(service=service), self.assertRaisesRegex(RuntimeError, 'OTHER_SERVICE_REPLACED'):
                UPDATE.validate_containers(self.containers, changed, NEW)
        candidate['gateway']['HostConfig']['PortBindings']['80/tcp'] = []
        with self.assertRaisesRegex(RuntimeError, 'PUBLISHED_PORTS_CHANGED'):
            UPDATE.validate_containers(self.containers, candidate, NEW)

    def test_wrong_running_image_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'FRONTEND_CONTAINER_IMAGE_MISMATCH'):
            UPDATE.validate_containers(self.containers, self.containers, NEW)

    def test_health_and_internal_port_checks(self):
        identifiers = b' '.join(name.encode() for name in UPDATE.SERVICES)
        for issue, code in (('health', 'SERVICES_NOT_HEALTHY'), ('port', 'INTERNAL_PORT_PUBLISHED'), ('duplicate', 'SERVICE_SET_MISMATCH')):
            items = copy.deepcopy(list(self.containers.values()))
            if issue == 'health':
                items[0]['State']['Health']['Status'] = 'unhealthy'
            elif issue == 'port':
                next(item for item in items if item['Config']['Labels']['com.docker.compose.service'] == 'postgres')['HostConfig']['PortBindings'] = {'5432/tcp': []}
            else:
                items[-1] = items[0]
            with self.subTest(issue=issue), patch.object(UPDATE, 'docker', side_effect=[identifiers, json.dumps(items).encode()]), self.assertRaisesRegex(RuntimeError, code):
                UPDATE.containers()

    def test_recreate_has_no_dependency_build_or_pull(self):
        with patch.object(UPDATE, 'docker') as docker:
            UPDATE.recreate_frontend({})
        self.assertEqual(docker.call_args.args[0][-11:], ['up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--force-recreate', '--wait', '--wait-timeout', '180', 'frontend'])

    def test_https_checks_validate_certificate_and_unauthenticated_boundaries(self):
        with patch.object(UPDATE, 'command', side_effect=[b'200', b'401', b'401', b'403', b'404']) as command:
            results = UPDATE.http_checks()
        self.assertEqual(len(results), 5)
        for call in command.call_args_list:
            arguments = call.args[0]
            self.assertNotIn('-k', arguments)
            self.assertNotIn('--insecure', arguments)
            self.assertIn('193.112.17.218:443:127.0.0.1', arguments)
            self.assertNotIn('Authorization', ' '.join(arguments))
        with patch.object(UPDATE, 'command', return_value=b'403'), self.assertRaisesRegex(RuntimeError, 'HTTPS_BOUNDARY_CHECK_FAILED'):
            UPDATE.http_checks()

    def test_file_changed_since_plan_is_never_overwritten(self):
        with patch.object(UPDATE, 'checked_file', return_value=b'concurrent update'), patch.object(UPDATE, 'exclusive_write') as write, self.assertRaisesRegex(RuntimeError, 'CONFIGURATION_FILE_CHANGED'):
            UPDATE.replace_file(Path('/opt/boyankb/docker-compose.tencent.yml'), b'original', b'proposed')
        write.assert_not_called()

    def test_command_errors_hide_output(self):
        class Result:
            returncode = 1
            stdout = b'sensitive'
            stderr = b'sensitive'
        with patch.object(UPDATE.subprocess, 'run', return_value=Result()), self.assertRaisesRegex(RuntimeError, '^COMMAND_FAILED$'):
            UPDATE.command(['docker', 'inspect'])
        self.assertEqual(UPDATE.error_code(ValueError('sensitive')), 'ValueError')
        self.assertEqual(UPDATE.error_code(RuntimeError('sensitive content')), 'RuntimeError')

    def deploy_fixture(self):
        original = {UPDATE.ROOT / 'docker-compose.tencent.yml': self.compose, UPDATE.ROOT / 'deployment/tencent/images.lock.json': json.dumps(self.lock).encode()}
        updated = UPDATE.updated_files(self.compose, self.lock, OLD, NEW)
        proposed = dict(zip(original, updated, strict=True))
        files = dict(original)
        candidate = copy.deepcopy(self.configuration)
        candidate['services']['frontend']['image'] = NEW
        after = copy.deepcopy(self.containers)
        after['frontend']['Id'] = 'frontend-new'
        after['frontend']['Config']['Image'] = NEW
        accounts = Mock()
        report = {'status': 'PREPARED', 'backup_directory': '/opt/boyankb/backups/fixture'}

        def replace(path, expected, payload):
            UPDATE.require(files[path] == expected, 'CONFIGURATION_FILE_CHANGED')
            files[path] = payload

        self.enterContext(patch('builtins.print'))
        self.enterContext(patch.object(UPDATE, 'replace_file', side_effect=replace))
        self.enterContext(patch.object(UPDATE, 'checked_file', return_value=b'private environment'))
        self.enterContext(patch.object(UPDATE, 'http_checks', return_value={'GET /login': 200}))
        arguments = (accounts, NEW, OLD, original, proposed, {}, self.containers, self.configuration, hashlib.sha256(b'private environment').hexdigest(), UPDATE.ROOT / 'backups/fixture/report.json', report)
        return arguments, files, original, proposed, candidate, after, report

    def test_successful_release_records_verified_without_rollback(self):
        arguments, files, original, proposed, candidate, after, report = self.deploy_fixture()
        with patch.object(UPDATE, 'configuration', return_value=candidate), patch.object(UPDATE, 'containers', return_value=after), patch.object(UPDATE, 'recreate_frontend') as recreate:
            UPDATE.deploy(*arguments)
        self.assertEqual(files, proposed)
        self.assertEqual(report['status'], 'VERIFIED')
        self.assertTrue(report['environment_unchanged'])
        recreate.assert_called_once_with({})

    def test_failed_start_restores_both_files_and_old_frontend(self):
        arguments, files, original, proposed, candidate, after, report = self.deploy_fixture()
        with patch.object(UPDATE, 'configuration', side_effect=[candidate, self.configuration]), patch.object(UPDATE, 'containers', return_value=self.containers), patch.object(UPDATE, 'recreate_frontend', side_effect=[RuntimeError('COMMAND_FAILED'), None]) as recreate, self.assertRaisesRegex(RuntimeError, '^FRONTEND_UPDATE_ROLLED_BACK$'):
            UPDATE.deploy(*arguments)
        self.assertEqual(files, original)
        self.assertEqual(report['status'], 'ROLLED_BACK')
        self.assertEqual(recreate.call_count, 2)

    def test_rollback_failure_keeps_recovery_paths_in_report(self):
        arguments, files, original, proposed, candidate, after, report = self.deploy_fixture()
        with patch.object(UPDATE, 'configuration', side_effect=[candidate, self.configuration]), patch.object(UPDATE, 'recreate_frontend', side_effect=RuntimeError('COMMAND_FAILED')), self.assertRaisesRegex(RuntimeError, '^FRONTEND_UPDATE_ROLLBACK_REQUIRED$'):
            UPDATE.deploy(*arguments)
        self.assertEqual(files, original)
        self.assertEqual(report['status'], 'ROLLBACK_REQUIRED')
        self.assertEqual(report['backup_directory'], '/opt/boyankb/backups/fixture')
        self.assertEqual(report['rollback_error'], 'COMMAND_FAILED')

    def test_late_environment_change_triggers_rollback_without_writing_environment(self):
        arguments, files, original, proposed, candidate, after, report = self.deploy_fixture()
        with patch.object(UPDATE, 'configuration', side_effect=[candidate, self.configuration]), patch.object(UPDATE, 'containers', side_effect=[after, self.containers]), patch.object(UPDATE, 'recreate_frontend') as recreate, patch.object(UPDATE, 'checked_file', return_value=b'concurrent environment change'), self.assertRaisesRegex(RuntimeError, '^FRONTEND_UPDATE_ROLLED_BACK$'):
            UPDATE.deploy(*arguments)
        self.assertEqual(files, original)
        self.assertEqual(report['error'], 'ENVIRONMENT_FILE_CHANGED')
        self.assertEqual(recreate.call_count, 2)


if __name__ == '__main__':
    unittest.main()
