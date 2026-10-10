import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock
import uuid


SPEC = importlib.util.spec_from_file_location('tencent_runtime', Path(__file__).with_name('tencent-runtime.py'))
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def container_fixture():
    locks = {name: 'sha256:' + format(index, '064x') for index, name in enumerate(sorted(MOD.RESTORE.SERVICES), 1)}
    items = []
    for name, image in locks.items():
        ports = {'8090/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18080'}]} if name == 'gateway' else {}
        items.append({'Id': format(len(items) + 1, '064x'), 'Image': image, 'Config': {'Labels': {'com.docker.compose.project': MOD.PROJECT, 'com.docker.compose.service': name}}, 'State': {'Running': True, 'Health': {'Status': 'healthy'}}, 'HostConfig': {'Memory': 100000000, 'NanoCpus': 1000000000, 'PidsLimit': 64, 'SecurityOpt': ['no-new-privileges:true'], 'PortBindings': copy.deepcopy(ports)}, 'NetworkSettings': {'Ports': ports, 'Networks': {MOD.PROJECT + '_backend': {}}}})
    return items, locks


def limit_environment():
    return {**{env: str(value) for env, value in MOD.LIMITS.values()}, 'CONCURRENCY_POOL_SIZE': '1', 'WEKNORA_CHAT_ATTACHMENT_OCR_CONCURRENCY': '1'}


def graph_fixture():
    source = {'knowledge_ids': [str(uuid.UUID(int=index)) for index in range(1, 20)], 'chunk_ids': [str(uuid.UUID(int=index)) for index in range(100, 127)], 'configuration': {'graph_enabled': True, 'extract_enabled': True}}
    return [dict(MOD.EXPECTED_GRAPH), {'total': 384, 'out_of_scope': 0, 'missing_required': 0}, {'relationships': 326, 'invalid_edges': 0}, source['knowledge_ids'][:10], source['chunk_ids'][:20], source]


class TencentRuntimeTests(unittest.TestCase):
    def test_import_does_not_execute_remote_checks(self):
        with mock.patch.object(MOD.RESTORE, 'command') as command:
            MOD.module('runtime_test_restore_import', 'tencent-restore.py')
        command.assert_not_called()

    def test_host_failure_precedes_any_checks_or_report_write(self):
        with mock.patch.object(MOD.ACCOUNTS, 'host_guard', side_effect=MOD.ACCOUNTS.HandoverError('blocked')), mock.patch.object(MOD, 'check') as check, mock.patch.object(MOD.ACCOUNTS, 'save_private') as save, mock.patch('sys.stdout', io.StringIO()) as output:
            self.assertEqual(MOD.main([]), 1)
            self.assertFalse(json.loads(output.getvalue())['verified'])
        check.assert_not_called()
        save.assert_not_called()

    def test_both_cloud_guards_run_before_report(self):
        with mock.patch.object(MOD.ACCOUNTS, 'host_guard') as first, mock.patch.object(MOD.RESTORE, 'host_guard') as second, mock.patch.object(MOD, 'check', return_value={'healthy_services': 9}), mock.patch.object(MOD.ACCOUNTS, 'save_private') as save, mock.patch('sys.stdout', io.StringIO()):
            self.assertEqual(MOD.main([]), 0)
        first.assert_called_once()
        second.assert_called_once()
        self.assertEqual(save.call_args.args[0], MOD.ROOT / 'runtime-report.json')
        self.assertTrue(save.call_args.args[1]['verified'])

    def test_loopback_and_https_only_port_layouts_pass(self):
        items, locks = container_fixture()
        self.assertEqual(len(MOD.validate_containers(items, locks)), 9)
        gateway = next(item for item in items if item['Config']['Labels']['com.docker.compose.service'] == 'gateway')
        for container_key in ('HostConfig', 'NetworkSettings'):
            key = 'PortBindings' if container_key == 'HostConfig' else 'Ports'
            gateway[container_key][key]['443/tcp'] = [{'HostIp': '0.0.0.0', 'HostPort': '443'}]
        self.assertEqual(len(MOD.validate_containers(items, locks)), 9)

    def test_foreign_project_unhealthy_and_image_changes_rejected(self):
        for mutation in ('project', 'image', 'health', 'extra', 'duplicate'):
            items, locks = container_fixture()
            if mutation == 'project':
                items[0]['Config']['Labels']['com.docker.compose.project'] = 'weknora-local'
            elif mutation == 'image':
                items[0]['Image'] = 'sha256:' + 'f' * 64
            elif mutation == 'health':
                items[0]['State']['Health']['Status'] = 'unhealthy'
            elif mutation == 'extra':
                items.append(copy.deepcopy(items[0]))
            else:
                items[1] = copy.deepcopy(items[0])
            with self.assertRaises(MOD.RESTORE.RestoreError):
                MOD.validate_containers(items, locks)

    def test_privileged_host_namespace_and_foreign_network_rejected(self):
        for field, value in (('Privileged', True), ('NetworkMode', 'host'), ('PidMode', 'host'), ('IpcMode', 'host'), ('Devices', [{'PathOnHost': '/dev/sda'}]), ('Memory', 0), ('NanoCpus', 0), ('PidsLimit', 0)):
            items, locks = container_fixture()
            items[0]['HostConfig'][field] = value
            with self.assertRaises(MOD.RESTORE.RestoreError):
                MOD.validate_containers(items, locks)
        items, locks = container_fixture()
        items[0]['NetworkSettings']['Networks'] = {'weknora-local_default': {}}
        with self.assertRaisesRegex(MOD.RESTORE.RestoreError, 'FOREIGN_NETWORK'):
            MOD.validate_containers(items, locks)

    def test_public_database_or_http_gateway_rejected(self):
        for service, port in (('postgres', '5432/tcp'), ('gateway', '8090/tcp')):
            items, locks = container_fixture()
            item = next(value for value in items if value['Config']['Labels']['com.docker.compose.service'] == service)
            ports = {port: [{'HostIp': '0.0.0.0', 'HostPort': '80'}]}
            item['HostConfig']['PortBindings'] = copy.deepcopy(ports)
            item['NetworkSettings']['Ports'] = ports
            with self.assertRaises(MOD.RESTORE.RestoreError):
                MOD.validate_containers(items, locks)

    def test_actual_ports_cannot_differ_from_configuration(self):
        items, locks = container_fixture()
        items[0]['NetworkSettings']['Ports'] = {'8080/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '8080'}]}
        with self.assertRaisesRegex(MOD.RESTORE.RestoreError, 'BINDINGS_DIFFER'):
            MOD.validate_containers(items, locks)

    def test_graph_totals_and_valid_subsets_pass(self):
        MOD.validate_graph(*graph_fixture())

    def test_graph_totals_scope_links_and_configuration_rejected(self):
        for mutation in ('count', 'scope', 'missing', 'edges', 'document', 'chunk', 'disabled'):
            values = graph_fixture()
            if mutation == 'count':
                values[0]['nodes'] = 385
            elif mutation == 'scope':
                values[1]['out_of_scope'] = 1
            elif mutation == 'missing':
                values[1]['missing_required'] = 1
            elif mutation == 'edges':
                values[2]['invalid_edges'] = 1
            elif mutation == 'document':
                values[3].append(str(uuid.UUID(int=200)))
            elif mutation == 'chunk':
                values[4].append(str(uuid.UUID(int=300)))
            else:
                values[5]['configuration']['extract_enabled'] = False
            with self.assertRaises(MOD.RESTORE.RestoreError):
                MOD.validate_graph(*values)

    def test_concurrency_environment_and_matching_db_settings_pass(self):
        settings = [{'key': key, 'valid': True, 'limit': expected} for key, (_, expected) in MOD.LIMITS.items()]
        result = MOD.validate_concurrency(settings, [{'valid': True, 'limit': 0}, {'valid': True, 'limit': 2}], limit_environment())
        self.assertEqual(result['database_overrides'], 7)
        self.assertFalse(result['actual_worker_capacity_verified'])
        self.assertFalse(result['interactive_chat_limited'])
        MOD.validate_concurrency([], [{'valid': True, 'limit': -1}], limit_environment())

    def test_high_db_or_model_override_rejected(self):
        with self.assertRaisesRegex(MOD.RESTORE.RestoreError, 'DATABASE_CONCURRENCY'):
            MOD.validate_concurrency([{'key': 'asynq.core_concurrency', 'valid': True, 'limit': 8}], [{'valid': True, 'limit': 0}], limit_environment())
        with self.assertRaisesRegex(MOD.RESTORE.RestoreError, 'MODEL_CONCURRENCY'):
            MOD.validate_concurrency([], [{'valid': True, 'limit': 3}], limit_environment())
        with self.assertRaisesRegex(MOD.RESTORE.RestoreError, 'MODEL_CONCURRENCY'):
            MOD.validate_concurrency([], [{'valid': False, 'limit': 0}], limit_environment())

    def test_changed_runtime_env_rejected(self):
        values = limit_environment()
        values['WEKNORA_MODEL_MAX_CONCURRENCY'] = '32'
        with self.assertRaisesRegex(MOD.RESTORE.RestoreError, 'CONCURRENCY_ENVIRONMENT'):
            MOD.validate_concurrency([], [{'valid': True, 'limit': 0}], values)

    def test_cypher_reads_credentials_in_container_not_arguments(self):
        with mock.patch.object(MOD.RESTORE, 'command', return_value=b'nodes\n384\n') as command:
            self.assertEqual(MOD.cypher({'Id': 'synthetic'}, 'MATCH (n) RETURN count(n) AS nodes;'), [{'nodes': '384'}])
        args = command.call_args.args[0]
        self.assertIn('--access-mode read', args[-1])
        self.assertIn('${NEO4J_AUTH#*/}', args[-1])
        self.assertNotIn('-p', args)
        self.assertIn('MATCH (n)', command.call_args.args[1].decode())

    def test_failed_checks_redact_exception_and_preserve_false_result(self):
        with mock.patch.object(MOD.ACCOUNTS, 'host_guard'), mock.patch.object(MOD.RESTORE, 'host_guard'), mock.patch.object(MOD, 'check', side_effect=ValueError('synthetic-secret')), mock.patch.object(MOD.ACCOUNTS, 'save_private') as save, mock.patch('sys.stdout', io.StringIO()) as output:
            self.assertEqual(MOD.main([]), 1)
            self.assertNotIn('synthetic-secret', output.getvalue())
            self.assertFalse(save.call_args.args[1]['verified'])


if __name__ == '__main__':
    unittest.main()
