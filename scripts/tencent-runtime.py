import argparse
import csv
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


ACCOUNTS = module('runtime_accounts', 'tencent-accounts.py')
RESTORE = module('runtime_restore', 'tencent-restore.py')
ROOT = RESTORE.ROOT
PROJECT = RESTORE.PROJECT
KB = RESTORE.KB
LABEL = 'ENTITY' + KB.replace('-', '_')
ROLES = {'boyankb/app': 'app', 'boyankb/frontend': 'frontend', 'boyankb/trial-gateway': 'gateway', 'boyankb/document-renderer': 'document-renderer', 'wechatopenai/weknora-docreader': 'docreader', 'paradedb/paradedb': 'postgres', 'redis': 'redis', 'ollama/ollama': 'ollama', 'neo4j': 'neo4j'}
LIMITS = {**{'asynq.' + pool + '_concurrency': ('WEKNORA_ASYNQ_' + pool.upper() + '_CONCURRENCY', 1) for pool in ('core', 'postprocess', 'enrichment', 'maintenance', 'shared')}, 'asynq.wiki_concurrency': ('WEKNORA_WIKI_ASYNQ_CONCURRENCY', 1), 'model.max_concurrency': ('WEKNORA_MODEL_MAX_CONCURRENCY', 2)}
EXPECTED_GRAPH = {'nodes': 384, 'relationships': 326}
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')


def require(condition, code):
    RESTORE.require(condition, code)


def environment(container):
    return dict(item.split('=', 1) for item in container['Config'].get('Env', []) if '=' in item)


def image_locks():
    value = json.loads(RESTORE.safe_path(ROOT / 'deployment/tencent/images.lock.json').read_bytes())
    require(value.get('platform') == 'linux/amd64' and len(value['images']) == 9, 'RUNTIME_IMAGE_LOCK_INVALID')
    result = {}
    for item in value['images']:
        role = ROLES.get(item['tag'].rsplit(':', 1)[0])
        require(role and role not in result and re.fullmatch(r'sha256:[0-9a-f]{64}', item['id']), 'RUNTIME_IMAGE_ROLE_INVALID')
        result[role] = item['id']
    require(set(result) == RESTORE.SERVICES, 'RUNTIME_IMAGE_ROLES_MISSING')
    return result


def port_bindings(container):
    result = set()
    for port, bindings in container['HostConfig'].get('PortBindings', {}).items():
        for binding in bindings or []:
            entry = (port, binding.get('HostIp'), binding.get('HostPort'))
            require(entry not in result, 'RUNTIME_DUPLICATE_PORT')
            result.add(entry)
    actual = {(port, item.get('HostIp'), item.get('HostPort')) for port, bindings in container['NetworkSettings'].get('Ports', {}).items() for item in bindings or []}
    require(actual == result, 'RUNTIME_PORT_BINDINGS_DIFFER')
    return result


def validate_containers(items, locked):
    require(len(items) == 9, 'RUNTIME_SERVICE_COUNT_INVALID')
    result = {}
    for item in items:
        labels = item['Config'].get('Labels', {})
        name = labels.get('com.docker.compose.service')
        require(labels.get('com.docker.compose.project') == PROJECT and name in RESTORE.SERVICES and name not in result, 'RUNTIME_SERVICE_IDENTITY_INVALID')
        require(item['Image'] == locked[name] and item['State'].get('Running') and item['State'].get('Health', {}).get('Status') == 'healthy', 'RUNTIME_SERVICE_UNHEALTHY_OR_IMAGE_CHANGED')
        host = item['HostConfig']
        require(not host.get('Privileged') and host.get('NetworkMode') != 'host' and host.get('PidMode') != 'host' and host.get('IpcMode') != 'host' and not host.get('Devices'), 'RUNTIME_HOST_ACCESS_REJECTED')
        require(host.get('Memory', 0) > 0 and host.get('NanoCpus', 0) > 0 and host.get('PidsLimit', 0) > 0, 'RUNTIME_RESOURCE_LIMIT_MISSING')
        require('no-new-privileges:true' in host.get('SecurityOpt', []), 'RUNTIME_SECURITY_OPTION_MISSING')
        networks = item['NetworkSettings'].get('Networks', {})
        require(networks and all(name.startswith(PROJECT + '_') for name in networks), 'RUNTIME_FOREIGN_NETWORK_REJECTED')
        ports = port_bindings(item)
        if name == 'gateway':
            base = {('8090/tcp', '127.0.0.1', '18080')}
            require(ports in (base, base | {('443/tcp', '0.0.0.0', '443')}), 'RUNTIME_GATEWAY_PORT_REJECTED')
        else:
            require(not ports, 'RUNTIME_INTERNAL_SERVICE_PUBLISHED')
        result[name] = item
    require(set(result) == RESTORE.SERVICES, 'RUNTIME_SERVICES_MISSING')
    return result


def containers(locked):
    ids = RESTORE.command(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + PROJECT]).decode().split()
    require(len(ids) == 9 and all(re.fullmatch(r'[0-9a-f]{12,64}', item) for item in ids), 'RUNTIME_RUNNING_SERVICE_COUNT_INVALID')
    return validate_containers(json.loads(RESTORE.command(['docker', 'inspect', *ids])), locked)


def query(postgres, sql):
    return json.loads(RESTORE.pg_query(postgres, sql))


def graph_sql(postgres):
    return query(postgres, "SELECT json_build_object('knowledge_ids',(SELECT coalesce(json_agg(id),'[]') FROM knowledges WHERE knowledge_base_id='" + KB + "' AND tenant_id=10000 AND deleted_at IS NULL),'chunk_ids',(SELECT coalesce(json_agg(id),'[]') FROM chunks WHERE knowledge_base_id='" + KB + "' AND tenant_id=10000 AND deleted_at IS NULL),'configuration',(SELECT json_build_object('graph_enabled',indexing_strategy::jsonb->'graph_enabled','extract_enabled',extract_config::jsonb->'enabled') FROM knowledge_bases WHERE id='" + KB + "' AND tenant_id=10000 AND deleted_at IS NULL));")


def cypher(neo4j, sql):
    args = ['docker', 'exec', '-i', neo4j['Id'], 'sh', '-ec', 'export NEO4J_USERNAME=neo4j NEO4J_PASSWORD="${NEO4J_AUTH#*/}"; exec cypher-shell -a bolt://127.0.0.1:7687 --format plain --access-mode read']
    data = RESTORE.command(args, (sql + '\n').encode(), timeout=90)
    rows = list(csv.reader(io.StringIO(data.decode()), skipinitialspace=True))
    require(rows and len(set(rows[0])) == len(rows[0]) and all(len(row) == len(rows[0]) for row in rows[1:]), 'CYPHER_RESPONSE_INVALID')
    return [dict(zip(rows[0], row)) for row in rows[1:]]


def single(rows):
    require(len(rows) == 1, 'CYPHER_SINGLE_RESULT_REQUIRED')
    return rows[0]


def validate_graph(counts, scope, edges, knowledge_ids, chunk_ids, source):
    require(counts == EXPECTED_GRAPH, 'RUNTIME_GRAPH_COUNTS_DIFFER')
    require(scope['total'] == 384 and scope['out_of_scope'] == 0 and scope['missing_required'] == 0 and edges == {'relationships': 326, 'invalid_edges': 0}, 'RUNTIME_GRAPH_SCOPE_INVALID')
    require(source['configuration'] == {'graph_enabled': True, 'extract_enabled': True}, 'RUNTIME_GRAPH_CONFIGURATION_DISABLED')
    require(len(source['knowledge_ids']) == 19 and len(source['chunk_ids']) == 27, 'RUNTIME_ENTERPRISE_DOCUMENT_OR_CHUNK_COUNT_CHANGED')
    require(all(UUID.fullmatch(item) for item in [*knowledge_ids, *chunk_ids, *source['knowledge_ids'], *source['chunk_ids']]), 'RUNTIME_GRAPH_REFERENCE_INVALID')
    require(knowledge_ids and chunk_ids and set(knowledge_ids).issubset(source['knowledge_ids']) and set(chunk_ids).issubset(source['chunk_ids']), 'RUNTIME_GRAPH_REFERENCE_OUTSIDE_ENTERPRISE')


def graph_checks(items):
    neo4j = items['neo4j']
    counts = {key: int(value) for key, value in single(cypher(neo4j, 'MATCH (n) WITH count(n) AS nodes OPTIONAL MATCH ()-[r]->() RETURN nodes,count(r) AS relationships;')).items()}
    scope = {key: int(value) for key, value in single(cypher(neo4j, "MATCH (n) RETURN count(n) AS total,sum(CASE WHEN '" + LABEL + "' IN labels(n) THEN 0 ELSE 1 END) AS out_of_scope,sum(CASE WHEN n.kg IS NULL OR n.name IS NULL OR n.chunks IS NULL THEN 1 ELSE 0 END) AS missing_required;")).items()}
    edges = {key: int(value) for key, value in single(cypher(neo4j, "MATCH (a)-[r]->(b) RETURN count(r) AS relationships,sum(CASE WHEN a.kg IS NULL OR b.kg IS NULL OR a.kg<>b.kg OR NOT '" + LABEL + "' IN labels(a) OR NOT '" + LABEL + "' IN labels(b) THEN 1 ELSE 0 END) AS invalid_edges;")).items()}
    knowledge_ids = [row['knowledge_id'] for row in cypher(neo4j, 'MATCH (n) RETURN DISTINCT n.kg AS knowledge_id;')]
    chunk_ids = [row['chunk_id'] for row in cypher(neo4j, 'MATCH (n) UNWIND n.chunks AS chunk_id RETURN DISTINCT chunk_id;')]
    source = graph_sql(items['postgres'])
    validate_graph(counts, scope, edges, knowledge_ids, chunk_ids, source)
    procedures = int(single(cypher(neo4j, "SHOW PROCEDURES YIELD name WHERE name IN ['apoc.merge.node','apoc.merge.relationship','apoc.periodic.iterate'] RETURN count(*) AS available;"))['available'])
    functions = int(single(cypher(neo4j, "SHOW FUNCTIONS YIELD name WHERE name='apoc.coll.union' RETURN count(*) AS available;"))['available'])
    require(procedures == 3 and functions == 1, 'RUNTIME_APOC_CAPABILITIES_MISSING')
    version = single(cypher(neo4j, 'CALL dbms.components() YIELD versions UNWIND versions AS version RETURN version;'))['version']
    require(re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.-]+)?', version), 'RUNTIME_NEO4J_VERSION_INVALID')
    return {**counts, 'enterprise_documents': 19, 'enterprise_chunks': 27, 'represented_documents': len(knowledge_ids), 'represented_chunks': len(chunk_ids), 'out_of_scope_nodes': 0, 'invalid_edges': 0, 'unknown_document_references': 0, 'unknown_chunk_references': 0, 'configuration_enabled': True, 'apoc_procedures_available': procedures, 'apoc_functions_available': functions, 'neo4j_version': version, 'apoc_version_verified': False, 'offline_consistency_verified': False}


def concurrency_sql(postgres):
    keys = ','.join("'" + key + "'" for key in sorted(LIMITS))
    settings = query(postgres, "SELECT coalesce(json_agg(json_build_object('key',key,'valid',value#>>'{}' ~ '^-?[0-9]{1,9}$','limit',CASE WHEN value#>>'{}' ~ '^-?[0-9]{1,9}$' THEN (value#>>'{}')::int ELSE NULL END)),'[]') FROM system_settings WHERE key IN (" + keys + ');')
    models = query(postgres, "SELECT coalesce(json_agg(json_build_object('valid',NOT parameters::jsonb ? 'max_concurrency' OR parameters::jsonb->>'max_concurrency' ~ '^-?[0-9]{1,9}$','limit',CASE WHEN parameters::jsonb->>'max_concurrency' ~ '^-?[0-9]{1,9}$' THEN (parameters::jsonb->>'max_concurrency')::int ELSE 0 END)),'[]') FROM models WHERE deleted_at IS NULL;")
    return settings, models


def validate_concurrency(settings, models, app_env):
    require(all(app_env.get(env_name) == str(expected) for env_name, expected in LIMITS.values()), 'RUNTIME_CONCURRENCY_ENVIRONMENT_CHANGED')
    require(app_env.get('CONCURRENCY_POOL_SIZE') == '1' and app_env.get('WEKNORA_CHAT_ATTACHMENT_OCR_CONCURRENCY') == '1', 'RUNTIME_DIRECT_CONCURRENCY_ENVIRONMENT_CHANGED')
    mapped = {row['key']: row for row in settings}
    require(len(mapped) == len(settings) and set(mapped).issubset(LIMITS), 'RUNTIME_CONCURRENCY_SETTING_KEYS_INVALID')
    require(all(row['valid'] is True and row['limit'] == LIMITS[key][1] for key, row in mapped.items()), 'RUNTIME_DATABASE_CONCURRENCY_OVERRIDE_CHANGED')
    require(models and all(row['valid'] is True and isinstance(row['limit'], int) and row['limit'] <= 2 for row in models), 'RUNTIME_MODEL_CONCURRENCY_OVERRIDE_CHANGED')
    return {'database_overrides': len(settings), 'effective_core': 1, 'effective_postprocess': 1, 'effective_enrichment': 1, 'effective_maintenance': 1, 'effective_shared': 1, 'effective_wiki': 1, 'effective_model_default': 2, 'models_checked': len(models), 'positive_model_overrides': sum(row['limit'] > 0 for row in models), 'actual_worker_capacity_verified': False, 'interactive_chat_limited': False}


def check():
    items = containers(image_locks())
    app_env = environment(items['app'])
    neo_env = environment(items['neo4j'])
    require(app_env.get('NEO4J_ENABLE') == 'true' and app_env.get('NEO4J_URI') == 'bolt://neo4j:7687' and app_env.get('NEO4J_USERNAME') == 'neo4j', 'RUNTIME_APP_GRAPH_CONFIGURATION_CHANGED')
    require(app_env.get('NEO4J_PASSWORD') and neo_env.get('NEO4J_AUTH') == 'neo4j/' + app_env['NEO4J_PASSWORD'], 'RUNTIME_APP_GRAPH_CREDENTIAL_MISMATCH')
    settings, models = concurrency_sql(items['postgres'])
    limits = validate_concurrency(settings, models, app_env)
    graph = graph_checks(items)
    return {'healthy_services': 9, 'locked_images': 9, 'privileged_services': 0, 'published_internal_services': 0, 'https_port_published': len(port_bindings(items['gateway'])) == 2, 'graph': graph, 'concurrency': limits, 'application_workflow_verified': False}


def main(argv=None):
    argparse.ArgumentParser().parse_args(argv)
    guarded = False
    report = {'format': 'boyankb-tencent-runtime-v1', 'verified': False}
    try:
        ACCOUNTS.host_guard()
        RESTORE.host_guard()
        guarded = True
        report.update(check(), verified=True)
        ACCOUNTS.save_private(ROOT / 'runtime-report.json', report, replace=True)
        print(json.dumps(report, sort_keys=True))
        return 0
    except (ACCOUNTS.HandoverError, RESTORE.RestoreError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        report['code'] = str(error) if isinstance(error, (ACCOUNTS.HandoverError, RESTORE.RestoreError)) else 'RUNTIME_LOCAL_STATE_INVALID'
        if guarded:
            try:
                ACCOUNTS.save_private(ROOT / 'runtime-report.json', report, replace=True)
            except (ACCOUNTS.HandoverError, OSError, ValueError):
                report['report_saved'] = False
        print(json.dumps(report, sort_keys=True))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
